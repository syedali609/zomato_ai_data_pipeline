import os
import json
from pathlib import Path

import snowflake.connector
import ollama
from dotenv import load_dotenv

# Local runs: read ../airflow/.env. In Docker the variables come from docker-compose.
load_dotenv(Path(__file__).resolve().parent.parent / "airflow" / ".env")

MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
BATCH_N = int(os.getenv("ENRICH_BATCH_SIZE", "5"))   # raise gradually after testing
TOPICS = ["food quality", "delivery", "pricing", "service", "packaging", "other"]

# Local Ollama needs no key. Only send one if you use Ollama's hosted cloud.
_key = os.getenv("OLLAMA_API_KEY")
_headers = {"Authorization": f"Bearer {_key}"} if _key else None
client = ollama.Client(host=OLLAMA_HOST, headers=_headers)

SYSTEM_PROMPT = f"""
You classify customer reviews for a food delivery app.

For the review you are given, return:
- sentiment_label: positive, negative, or neutral
- sentiment_score: a number between -1.0 and 1.0
- topic: one of {TOPICS}
- key_issue: a short phrase of 6 words or less describing the main issue, or null if there is no issue

Reply with ONLY a JSON object, no other text, in this exact format:
{{
    "sentiment_label": "<sentiment_label>",
    "sentiment_score": <sentiment_score>,
    "topic": "<topic>",
    "key_issue": "<key_issue or null>"
}}
"""


def get_connection():
    return snowflake.connector.connect(
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database=os.getenv("SNOWFLAKE_DATABASE", "ZOMATO"),
        schema=os.getenv("SNOWFLAKE_SCHEMA", "AI"),
        role=os.getenv("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
    )


def create_output_table(cursor):
    cursor.execute("CREATE SCHEMA IF NOT EXISTS ZOMATO.AI")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ZOMATO.AI.REVIEW_ENRICHED (
            REVIEW_ID STRING,
            SENTIMENT_LABEL STRING,
            SENTIMENT_SCORE FLOAT,
            TOPIC STRING,
            KEY_ISSUE STRING,
            MODEL STRING,
            ENRICHED_AT TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
        )
    """)


def get_reviews_to_enrich(cursor):
    cursor.execute(f"""
        SELECT r.REVIEW_ID, r.COMMENT
        FROM ZOMATO.RAW.REVIEWS r
        WHERE r.COMMENT IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM ZOMATO.AI.REVIEW_ENRICHED e
              WHERE e.REVIEW_ID = r.REVIEW_ID
          )
        LIMIT {BATCH_N}
    """)
    return cursor.fetchall()


def classify_review(comment):
    response = client.chat(
        model=MODEL,
        format="json",
        options={"temperature": 0},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": comment},
        ],
    )
    text = response["message"]["content"]
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def save_results(cursor, results):
    print(f"Saving {len(results)} enriched reviews to Snowflake...")
    cursor.executemany(
        """
        INSERT INTO ZOMATO.AI.REVIEW_ENRICHED
            (review_id, sentiment_label, sentiment_score, topic, key_issue, model)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        results,
    )


def main():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        create_output_table(cursor)
        reviews = get_reviews_to_enrich(cursor)

        if len(reviews) == 0:
            print("No new reviews to enrich.")
            return

        print(f"Enriching {len(reviews)} reviews with {MODEL} at {OLLAMA_HOST}...")

        results = []
        for review_id, comment in reviews:
            print(f"Classifying review {review_id}: {comment}")
            try:
                labels = classify_review(comment)
                print(f"Labels for review {review_id}: {labels}")

                key_issue = labels.get("key_issue")
                if key_issue in (None, "", "null", "None"):
                    key_issue = None

                results.append((
                    review_id,
                    labels.get("sentiment_label"),
                    labels.get("sentiment_score"),
                    labels.get("topic"),
                    key_issue,
                    MODEL,
                ))
            except Exception as e:
                print(f"Error occurred while classifying review {review_id}: {e}")

        if not results:
            raise RuntimeError("All classifications failed; nothing to save.")

        save_results(cursor, results)
        conn.commit()
        print(f"Saved {len(results)} enriched reviews to Snowflake.")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
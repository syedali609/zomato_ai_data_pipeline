import os
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import snowflake.connector
import ollama
from dotenv import load_dotenv

# Local runs read ../airflow/.env; in Docker the variables come from docker-compose.
load_dotenv(Path(__file__).resolve().parent.parent / "airflow" / ".env")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBEDDING_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
CHAT_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
NEW_REVIEWS = int(os.getenv("CHAT_REVIEW_COUNT", "500"))
TOP_K = 5
# Cache name includes the embedding model, so vectors from different models never mix
CACHE_FILE = f"review_embeddings_{EMBEDDING_MODEL.replace(':', '_')}_{NEW_REVIEWS}.parquet"

client = ollama.Client(host=OLLAMA_HOST)


def read_reviews_from_snowflake():
    conn = snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "ZOMATO_WH"),
        database=os.getenv("SNOWFLAKE_DATABASE", "ZOMATO"),
        schema=os.getenv("SNOWFLAKE_SCHEMA", "STAGING"),
        role=os.getenv("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
    )
    query = f"""
        SELECT REVIEW_ID, CITY, RATING, COMMENT
        FROM ZOMATO.STAGING.STG_REVIEWS
        WHERE COMMENT IS NOT NULL
        ORDER BY RANDOM()
        LIMIT {NEW_REVIEWS}
    """
    cur = conn.cursor()
    try:
        df = cur.execute(query).fetch_pandas_all()
    finally:
        cur.close()
        conn.close()

    df.columns = [col.lower() for col in df.columns]
    return df


def embed(texts, batch_size=50):
    """Embed a list of texts with Ollama, in small batches."""
    vectors = []
    for i in range(0, len(texts), batch_size):
        response = client.embed(model=EMBEDDING_MODEL, input=texts[i:i + batch_size])
        vectors.extend(response["embeddings"])
    return vectors


@st.cache_data(show_spinner="Embedding reviews (first run only)...")
def load_reviews():
    if os.path.exists(CACHE_FILE):
        return pd.read_parquet(CACHE_FILE)

    df = read_reviews_from_snowflake()
    df["embedding"] = embed(df["comment"].tolist())
    df.to_parquet(CACHE_FILE)
    return df


def find_similar_reviews(question, df):
    question_vector = np.array(embed([question])[0])
    matrix = np.vstack(df["embedding"].to_numpy())

    # cosine similarity against all reviews at once
    scores = matrix @ question_vector / (
        np.linalg.norm(matrix, axis=1) * np.linalg.norm(question_vector)
    )

    df = df.copy()
    df["score"] = scores
    return df.nlargest(TOP_K, "score")


def ask_llm(question, top_reviews):
    context = ""
    for _, row in top_reviews.iterrows():
        context += f"- ({row['city']}, {row['rating']} stars) {row['comment']}\n"

    system_prompt = (
        "Answer ONLY using the customer reviews provided. "
        "Be concise. If the reviews don't cover it, say so."
    )
    user_prompt = f"Question: {question}\n\nReviews:\n{context}"

    response = client.chat(
        model=CHAT_MODEL,
        options={"temperature": 0.2},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response["message"]["content"]


st.title("Chat with your Zomato Reviews")
st.caption(f"Searching {NEW_REVIEWS} reviews, answering with {CHAT_MODEL} (Ollama)")

try:
    review_df = load_reviews()
except Exception as e:
    st.error(f"Could not load reviews: {e}")
    st.stop()

question = st.text_input(
    "Ask a question about your reviews:",
    placeholder="e.g. What are the most common complaints about delivery?",
)

if question:
    try:
        top_reviews = find_similar_reviews(question, review_df)
        answer = ask_llm(question, top_reviews)
    except Exception as e:
        st.error(f"Ollama error: {e}")
        st.stop()

    st.markdown("**Answer:**")
    st.write(answer)

    with st.expander("Reviews used to build this answer"):
        st.dataframe(top_reviews[["city", "rating", "comment"]], hide_index=True)
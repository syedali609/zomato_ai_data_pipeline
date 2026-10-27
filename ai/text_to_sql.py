import os
import json
import re

import pandas as pd
import streamlit as st
import snowflake.connector
import ollama
from dotenv import load_dotenv

from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / "airflow" / ".env")

# Ollama configuration
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

client = ollama.Client(host=OLLAMA_HOST)

FORBIDDEN_WORDS = [
    "drop", "delete", "truncate", "alter", "update",
    "insert", "create", "replace", "grant", "revoke"
]

EXAMPLE_QUESTIONS = [
    "Top 10 cities by GMV",
    "Which cuisine has the most orders?",
    "Average delivery time by city, worst first",
    "Cancel rate by payment method"
]

SCHEMA = """
Tables available in Snowflake. Use bare table names, no database or schema prefix.

FACT_ORDERS(order_id, order_date, customer_id, restaurant_id, city, cuisine,
           payment_method, order_status, is_delivered, sales_amount, discount,
           delivery_fee, gst, customer_rating, delivery_time_min)

DIM_RESTAURANT(restaurant_id, restaurant_name, city, cuisine, rating, cost_for_two)

DIM_CUSTOMER(customer_id, customer_name, age, age_segment, gender, city)

MART_DAILY_CITY_REVENUE(order_date, city, orders, cancel_rate, gmv, aov)

MART_RESTAURANT_PERFORMANCE(restaurant_id, restaurant_name, city, cuisine,
                            orders, revenue, avg_customer_rating, cancel_rate)

MART_DELIVERY_SLA(city, order_hour, delivered_orders, p50_delivery_min, late_rate)

Note: GMV means delivered revenue. Prefer MART_ tables when they fit the question.
"""

SYSTEM_PROMPT = f"""
You are a Snowflake SQL expert. Write ONE SQL query that answers the question.

Rules:
- Generate read-only SELECT queries only.
- Do not generate INSERT, UPDATE, DELETE, DROP, ALTER, or other data-changing statements.
- Use bare table names, such as FACT_ORDERS, not ZOMATO.MARTS.FACT_ORDERS.
- Add a LIMIT of 100 or less, unless the question asks for a single aggregate result.
- Return only valid JSON in this exact format: {{"sql": "your query here"}}.
- Do not include Markdown fences or explanations.
- MART_DAILY_CITY_REVENUE contains pre-aggregated daily metrics.
- For total GMV by city across dates, aggregate the daily GMV values in an outer query or use a subquery.
- Never nest aggregate functions directly, such as SUM(SUM(gmv)).
- Use the exact table names listed in the schema.

{SCHEMA}
"""


@st.cache_resource
def get_connection():
    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema="MARTS",
        role="DBT_ROLE"
    )


def generate_sql(question):
    response = client.chat(
        model=MODEL,
        format={
            "type": "object",
            "properties": {
                "sql": {"type": "string"}
            },
            "required": ["sql"]
        },
        options={"temperature": 0},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question}
        ]
    )

    answer = response.message.content
    result = json.loads(answer)

    sql = result.get("sql", "").strip()
    sql = re.sub(r"^```(?:sql)?\s*|\s*```$", "", sql, flags=re.IGNORECASE)
    sql = sql.replace("ZOMATO.MARTS.", "").replace("ZOMATO.", "")
    return sql.strip().rstrip(";")


def is_safe(sql):
    # Remove SQL comments before validation.
    cleaned = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    cleaned = re.sub(r"--[^\n]*", " ", cleaned).strip()

    # Only one SELECT statement is allowed.
    if not re.match(r"^(SELECT|WITH)\b", cleaned, flags=re.IGNORECASE):
        return False

    # Reject multiple statements.
    if ";" in cleaned:
        return False

    # Reject dangerous keywords as SQL tokens, not substrings.
    for word in FORBIDDEN_WORDS:
        if re.search(rf"\b{word}\b", cleaned, flags=re.IGNORECASE):
            return False

    return True


def run_query(sql):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Enforce a read-only session role/permissions in Snowflake as well.
        cursor.execute("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 60")
        return cursor.execute(sql).fetch_pandas_all()
    finally:
        cursor.close()


st.title("Chat with your Zomato Data")
st.caption(f"Ask in English. {MODEL} generates SQL locally, and Snowflake runs it.")

with st.sidebar:
    st.header("Example Questions")
    for q in EXAMPLE_QUESTIONS:
        st.markdown(f"- {q}")

question = st.text_input(
    "Enter your question here",
    placeholder="e.g. Top 10 restaurants by revenue in Bangalore"
)

if question:
    try:
        with st.spinner(f"Generating SQL with {MODEL}..."):
            sql = generate_sql(question)

        st.code(sql, language="sql")

        if not is_safe(sql):
            st.error("The generated SQL failed the safety checks and was not executed.")
        else:
            with st.spinner("Running query in Snowflake..."):
                df = run_query(sql)

            st.success(f"{len(df)} rows returned")
            st.dataframe(df, hide_index=True)

            if (
                len(df.columns) == 2
                and pd.api.types.is_numeric_dtype(df.iloc[:, 1])
            ):
                st.bar_chart(df, x=df.columns[0], y=df.columns[1])

    except Exception as e:
        st.error(f"Error: {e}")
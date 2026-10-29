# Zomato Data Platform

End-to-end batch pipeline on **35M+ rows** of food-delivery data: **S3 → Snowflake → dbt → Airflow → LLM enrichment → RAG chat**.

![Snowflake](https://img.shields.io/badge/Snowflake-29B5E8?logo=snowflake&logoColor=white)
![dbt](https://img.shields.io/badge/dbt-FF694B?logo=dbt&logoColor=white)
![Airflow](https://img.shields.io/badge/Airflow-017CEE?logo=apacheairflow&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![AWS S3](https://img.shields.io/badge/AWS_S3-569A31?logo=amazons3&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?logo=streamlit&logoColor=white)

![Airflow DAG](docs/images/airflow-dag.png)

## Architecture

```mermaid
flowchart LR
    S3[(S3 raw CSVs)] -->|COPY INTO| RAW[(Snowflake RAW)]
    RAW --> STG[dbt staging]
    STG --> MARTS[dbt marts]
    STG --> LLM[LLM enrichment]
    LLM --> AI[(AI.REVIEW_ENRICHED)]
    AI --> INS[mart_review_insights]
    STG --> CHAT[Streamlit RAG chat]
```

Airflow runs it daily: `reload_raw → dbt_build_core → enrich_reviews → dbt_build_ai`

## Highlights

- **35.1M rows** loaded: 23M order items, 10M orders, 1.2M menu rows, 300K reviews and more
- **17 dbt models** (7 staging, 10 marts) with 12 tests: dimensions, incremental facts and business marts
- **Secure S3 access** through a Snowflake storage integration (IAM role + external ID), with no stored AWS keys
- **Incremental by design:** `COPY INTO` skips loaded files, and enrichment only processes new reviews
- **AI isolated from core:** AI models carry `tag:ai`, so the core pipeline runs even if enrichment is down
- **No secrets in the repo:** credentials come from `.env`, dbt `env_var()` and Compose variables

| Snowflake | S3 source |
|---|---|
| ![Snowflake](docs/images/snowflake.png) | ![S3](docs/images/s3-raw.png) |

## Structure

```
airflow/   DAG, Dockerfile, docker-compose
zomato/    dbt project (staging + marts)
ai/        enrich_reviews.py, rag_chat.py, text_to_sql.py
```

## Run it

```bash
cp .env.example airflow/.env        # fill in your values
cd airflow && docker compose up -d  # Airflow at http://localhost:8080
streamlit run ai/rag_chat.py        # review chat (needs Ollama running)
```

Add `zomato/profiles.yml` using `env_var()` for credentials (it is git-ignored). Snowflake setup (database, storage integration, stage, `DBT_ROLE`) is described in the dbt and DAG files.

## Limitations

Local-LLM enrichment is slow, so reviews are processed in batches rather than all 300K at once. The chat app searches a sample of reviews held in memory, not a vector index.

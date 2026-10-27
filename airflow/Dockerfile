
# Airflow 3.x image with Snowflake provider, FAB auth, Ollama, and dbt
FROM apache/airflow:3.0.3

# Providers and Python client for local Ollama
RUN pip install --no-cache-dir \
      apache-airflow-providers-snowflake \
      apache-airflow-providers-fab \
      ollama

# Keep dbt isolated from Airflow's Python dependencies
RUN python -m venv /opt/airflow/dbt_venv && \
    /opt/airflow/dbt_venv/bin/pip install --no-cache-dir "dbt-snowflake==1.8.*"

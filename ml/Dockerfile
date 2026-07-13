FROM python:3.11-slim

WORKDIR /srv/ml

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ml ./ml

ENV ML_MODEL_DIR=/app/models \
    ML_DB_PATH=/data/drishti.db \
    ML_TOPOLOGY_PATH=/data/topology.json

EXPOSE 8200

CMD ["python", "-m", "ml.main"]

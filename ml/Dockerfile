FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/ml

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The build context (./ml) is the package's own contents (config.py, features.py,
# service/, etc.) rather than a parent dir containing an `ml/` subfolder — so we
# copy the context into an `ml/` package dir inside the image to make it importable
# as `ml.*` (matching `CMD ["python", "-m", "ml.main"]` below).
COPY . ./ml

ENV ML_MODEL_DIR=/app/models \
    ML_DB_PATH=/data/drishti.db \
    ML_TOPOLOGY_PATH=/data/topology.json

EXPOSE 8200

CMD ["python", "-m", "ml.main"]

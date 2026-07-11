FROM python:3.11-slim

WORKDIR /srv/backend

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DRISHTI_DB_PATH=/data/drishti.db \
    DRISHTI_TOPOLOGY_PATH=/data/topology.json

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

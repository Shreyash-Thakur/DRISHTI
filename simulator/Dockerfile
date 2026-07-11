FROM python:3.11-slim

WORKDIR /srv/simulator

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sim ./sim

ENV SIM_BACKEND_URL=http://backend:8000 \
    SIM_TOPOLOGY_PATH=/data/topology.json

EXPOSE 8100
CMD ["python", "-m", "sim.main"]

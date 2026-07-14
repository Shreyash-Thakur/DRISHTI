FROM python:3.11-slim

WORKDIR /srv/rca

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The build context (./rca) is the package's own contents (config.py, graph.py,
# correlate.py, service/, etc.) rather than a parent dir containing an `rca/`
# subfolder — so we copy the context into an `rca/` package dir inside the image
# to make it importable as `rca.*` (matching `CMD ["python", "-m", "rca.main"]`).
COPY . ./rca

ENV RCA_TOPOLOGY_PATH=/data/topology.json

EXPOSE 8300

CMD ["python", "-m", "rca.main"]

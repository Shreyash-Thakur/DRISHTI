FROM python:3.11-slim

WORKDIR /srv/copilot

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The build context (./copilot) is the package's own contents (config.py, llm.py,
# rag.py, service/, etc.) rather than a parent dir containing a `copilot/` subfolder
# — so we copy the context into a `copilot/` package dir inside the image to make it
# importable as `copilot.*` (matching `CMD ["python", "-m", "copilot.main"]`).
COPY . ./copilot

ENV COPILOT_RUNBOOKS_DIR=/data/runbooks

EXPOSE 8400

CMD ["python", "-m", "copilot.main"]

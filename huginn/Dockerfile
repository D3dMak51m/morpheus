FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
# Stage 22 — RSS scraper (knowledge-only) lives at the project root; importable
# as top-level `test_rss` because `python -m app.main` puts /app on sys.path.
COPY test_rss.py .

CMD ["python", "-m", "app.main"]

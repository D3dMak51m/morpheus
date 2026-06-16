FROM python:3.11-slim

WORKDIR /app

# tesseract: OCR for text-in-image posts (Telegram "text cards") that the small VLM
# can't read. Cyrillic + the swarm's target languages.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        tesseract-ocr \
        tesseract-ocr-eng tesseract-ocr-rus tesseract-ocr-ukr \
        tesseract-ocr-kaz tesseract-ocr-kir tesseract-ocr-tgk \
        tesseract-ocr-uzb tesseract-ocr-uzb-cyrl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

CMD ["python", "-m", "app.main"]

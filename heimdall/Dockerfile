FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# faster-whisper downloads its models from the HF hub; persist them under /models
# (a named volume) so switching STT_MODEL (e.g. medium -> large-v3) downloads once
# and survives restarts/rebuilds.
ENV HF_HOME=/models

# ffmpeg: robust decoding of ANY audio container/codec (ogg/opus voice notes, m4a,
# amr, mp3, aac, …) as a fallback path; PyAV handles most formats natively.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8004

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]

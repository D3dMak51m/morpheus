"""
HEIMDALL — Speech-to-Text Service
===================================
The all-hearing watchman of the swarm. A dedicated, self-contained STT microservice
(its own Docker container) so the rest of the system can "hear" any audio — voice
notes, music, video sound — in any container/codec.

Engine: faster-whisper (CTranslate2). Runs on CPU/int8 by design so it never fights
the single ~6 GB GPU that Ollama uses for generation. The model size is configurable
via ``STT_MODEL`` (default ``medium`` for cheap local dev; set ``large-v3`` in
production for the best quality on low-resource / dialect-heavy languages). Models are
downloaded once to the /models volume (HF_HOME) and reused.

Language coverage (Whisper): strong for ru / uk / en, decent for uz / kk / tg, weak
for ky / tk — pass an explicit ``language`` to force decoding when the channel's
language is known (helps the divergent Uzbek/Tajik dialects too).
"""

import logging
import os
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from faster_whisper import WhisperModel

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("heimdall")

STT_MODEL = os.getenv("STT_MODEL", "medium")
STT_DEVICE = os.getenv("STT_DEVICE", "cpu")
STT_COMPUTE = os.getenv("STT_COMPUTE_TYPE", "int8")
STT_BEAM = int(os.getenv("STT_BEAM_SIZE", "5"))
# Cap how long an audio we bother decoding (defensive against huge files).
MAX_UPLOAD_MB = int(os.getenv("STT_MAX_UPLOAD_MB", "50"))

app = FastAPI(title="HEIMDALL — STT", version="1.0")

_model: Optional[WhisperModel] = None


def get_model() -> WhisperModel:
    """Lazy-load (and cache) the Whisper model. First call downloads it to /models."""
    global _model
    if _model is None:
        logger.info("Loading faster-whisper '%s' (%s / %s)…", STT_MODEL, STT_DEVICE, STT_COMPUTE)
        _model = WhisperModel(STT_MODEL, device=STT_DEVICE, compute_type=STT_COMPUTE)
        logger.info("Model '%s' loaded.", STT_MODEL)
    return _model


@app.on_event("startup")
def _warm() -> None:
    try:
        get_model()
    except Exception as exc:  # don't crash the container; first request will retry
        logger.error("Model warm-load failed (will retry on first request): %s", exc)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": STT_MODEL, "device": STT_DEVICE, "compute": STT_COMPUTE,
            "loaded": _model is not None}


def _to_wav(src_path: str) -> str:
    """Fallback: transcode any container/codec to 16 kHz mono WAV via ffmpeg."""
    out = src_path + ".16k.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", src_path, "-ar", "16000", "-ac", "1", out],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return out


@app.post("/api/v1/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
) -> dict:
    """
    Transcribe an uploaded audio file (any format). ``language`` (ISO code like
    ``uz``/``kk``/``tg``/``ky``/``tk``/``ru``/``uk``/``en``) forces the decoding
    language; omit it for auto-detection.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_UPLOAD_MB} MB.")

    suffix = os.path.splitext(file.filename or "")[1] or ".bin"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    extra = []
    try:
        tmp.write(data)
        tmp.close()
        model = get_model()
        lang = (language or "").strip().lower() or None

        def _run(path: str):
            segments, info = model.transcribe(path, language=lang, beam_size=STT_BEAM, vad_filter=True)
            return " ".join(s.text.strip() for s in segments).strip(), info

        try:
            text, info = _run(tmp.name)
        except Exception as decode_exc:
            # Some exotic containers fail PyAV decoding → transcode and retry once.
            logger.warning("Direct decode failed (%s); transcoding via ffmpeg.", decode_exc)
            wav = _to_wav(tmp.name)
            extra.append(wav)
            text, info = _run(wav)

        logger.info("Transcribed %s: lang=%s p=%.2f dur=%.1fs (%d chars).",
                    file.filename, info.language, info.language_probability, info.duration, len(text))
        return {
            "text": text,
            "language": info.language,
            "language_probability": round(float(info.language_probability), 3),
            "duration": round(float(info.duration), 2),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Transcription failed.")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}")
    finally:
        for p in [tmp.name, *extra]:
            try:
                os.unlink(p)
            except OSError:
                pass

"""
MYRMIDON — Media Reader
=========================
Turns the media attached to a post into text the bot can reason about:
  - audio (voice notes / music / video sound) → HEIMDALL STT service (any format),
  - images (photos, incl. every photo of an album) → Ollama VLM (Moondream).

MYRMIDON already holds the downloaded media bytes (it read the post on its Pyrogram
session), so it does the enrichment here and hands ORPHEUS a compact ``media_context``
string — ORPHEUS just weaves it into the comment prompt. STT lives in its own service
(HEIMDALL); the VLM stays on host Ollama with keep_alive=0 like the rest.
"""

import base64
import logging
import os
import subprocess

import httpx

logger = logging.getLogger("myrmidon.media_reader")

HEIMDALL_URL = os.getenv("HEIMDALL_URL", "http://heimdall:8004")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
VISION_MODEL_NAME = os.getenv("VISION_MODEL_NAME", "moondream:latest")

# OCR for text-in-image posts (the small VLM hallucinates on "text cards"). Multi-lang
# Tesseract; a text-dominant image is treated as its text, not a (made-up) scene.
OCR_LANGS = os.getenv("OCR_LANGS", "rus+eng+ukr+kaz+kir+tgk+uzb+uzb_cyrl")
OCR_MIN_CHARS = int(os.getenv("OCR_MIN_CHARS", "15"))

STT_TIMEOUT = int(os.getenv("HEIMDALL_TIMEOUT_SEC", "180"))
VLM_TIMEOUT = int(os.getenv("VLM_TIMEOUT_SEC", "90"))
# Bound the cost: a post can be a 10-photo album, but we only need a couple to react.
MAX_IMAGES = int(os.getenv("MEDIA_MAX_IMAGES", "4"))
MAX_AUDIO = int(os.getenv("MEDIA_MAX_AUDIO", "2"))


def _transcribe(path: str) -> tuple[str, str]:
    """Send one audio file to HEIMDALL. Returns (text, language) — ('', '') on failure."""
    try:
        with open(path, "rb") as f:
            files = {"file": (os.path.basename(path) or "audio", f, "application/octet-stream")}
            with httpx.Client(timeout=STT_TIMEOUT) as client:
                resp = client.post(f"{HEIMDALL_URL}/api/v1/transcribe", files=files)
                resp.raise_for_status()
                data = resp.json()
        return (data.get("text") or "").strip(), (data.get("language") or "")
    except Exception as exc:
        logger.warning("media_reader: HEIMDALL transcription failed: %s", exc)
        return "", ""


def _ocr(path: str) -> str:
    """Extract text from an image via Tesseract (multi-language). '' on none/failure."""
    try:
        out = subprocess.run(
            ["tesseract", path, "stdout", "-l", OCR_LANGS],
            capture_output=True, timeout=60,
        )
        text = (out.stdout or b"").decode("utf-8", "ignore")
        return " ".join(text.split()).strip()  # collapse whitespace/newlines
    except Exception as exc:
        logger.warning("media_reader: OCR failed: %s", exc)
        return ""


def _describe(path: str) -> str:
    """
    Turn one image into text. A "text card" (text rendered on an image — common in
    Telegram) is read by OCR, because the small VLM can't read it and hallucinates a
    scene. A real photo (little/no text) is described by the VLM. Mixed → both.
    """
    ocr = _ocr(path)
    if len(ocr) >= OCR_MIN_CHARS:
        # Text-dominant image → the words ARE the message; don't trust the VLM here.
        return f"на изображении текст: «{ocr[:400]}»"
    desc = _vlm(path)
    if ocr and desc:
        return f"{desc} (текст на фото: «{ocr[:200]}»)"
    if ocr:
        return f"текст на фото: «{ocr}»"
    return desc


def _vlm(path: str) -> str:
    """Describe one image via the Ollama VLM (Moondream). '' on failure."""
    try:
        with open(path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
        payload = {
            "model": VISION_MODEL_NAME,
            # Moondream is a small VLM — it answers simple prompts well but tends to
            # return empty on long/structured ones, so keep it short and direct.
            "prompt": "Describe this image in detail. What objects, people, or text do you see?",
            "stream": False,
            "keep_alive": 0,  # free VRAM right after — the GPU is shared with the LLM
            "images": [img_b64],
        }
        with httpx.Client(timeout=VLM_TIMEOUT) as client:
            resp = client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
            resp.raise_for_status()
            return (resp.json().get("response") or "").strip()
    except Exception as exc:
        logger.warning("media_reader: VLM description failed: %s", exc)
        return ""


def enrich(items: list[dict]) -> str:
    """
    ``items``: [{"path": str, "kind": "image"|"audio"}]. Returns a compact media-context
    string (one line per audio transcript / image description), or '' if nothing useful.
    Runs synchronously (call it from an executor in the async driver).
    """
    audios = [i for i in items if i.get("kind") == "audio"][:MAX_AUDIO]
    images = [i for i in items if i.get("kind") == "image"][:MAX_IMAGES]
    lines: list[str] = []

    for idx, a in enumerate(audios, 1):
        text, lang = _transcribe(a["path"])
        if text:
            tag = f" [{lang}]" if lang else ""
            label = f"[Аудио{idx}{tag}]" if len(audios) > 1 else f"[Аудио{tag}]"
            lines.append(f"{label}: {text}")

    for idx, im in enumerate(images, 1):
        desc = _describe(im["path"])
        if desc:
            label = f"[Фото{idx}]" if len(images) > 1 else "[Фото]"
            lines.append(f"{label}: {desc}")

    return "\n".join(lines)

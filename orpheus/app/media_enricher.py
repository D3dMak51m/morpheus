"""
ORPHEUS — Media Enrichment Module
====================================
Handles extraction and analysis of media content from raw events:
  - Video: Frame extraction (OpenCV/ffmpeg), audio transcription (faster-whisper),
    visual description (Moondream2 via Ollama VLM API).
  - Image: Visual description via Moondream2.

VRAM Scheduling (from tech_spec.md):
  When a media task arrives:
    1. Pause text generation queue (Celery).
    2. Unload LLM (Qwen 3B) from VRAM via Ollama API.
    3. Load VLM (Moondream2) into VRAM.
    4. Process all frames → generate visual descriptions.
    5. Unload VLM.
    6. Reload LLM.
    7. Resume text queue.

  faster-whisper always runs on CPU — zero VRAM impact.
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import cv2
import httpx
import numpy as np

logger = logging.getLogger("orpheus.media_enricher")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
VISION_MODEL_NAME = os.getenv("VISION_MODEL_NAME", "moondream:latest")
TEXT_MODEL_NAME = os.getenv("TEXT_MODEL_NAME", "qwen2.5:3b")

# Frame extraction settings
MAX_FRAMES_PER_VIDEO = 5
FRAME_EXTRACT_INTERVAL_SEC = 10


class MediaEnricher:
    """
    Processes media files (video/image) attached to raw events.
    Returns a textual summary combining audio transcription and visual descriptions.
    """

    def __init__(self) -> None:
        self._whisper_model = None

    def _get_whisper_model(self):
        """Lazy-load the faster-whisper model on CPU."""
        if self._whisper_model is None:
            try:
                from faster_whisper import WhisperModel

                logger.info("Loading faster-whisper 'small' model on CPU...")
                self._whisper_model = WhisperModel(
                    "small",
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=os.cpu_count() or 4,
                )
                logger.info("faster-whisper loaded successfully (CPU, int8).")
            except ImportError:
                logger.warning("faster-whisper not installed — STT disabled.")
            except Exception as exc:
                logger.error("Failed to load faster-whisper: %s", exc)
        return self._whisper_model

    def enrich(self, media_path: str, media_type: str) -> str:
        """
        Main enrichment entry point.

        Args:
            media_path: Path to the media file.
            media_type: Either 'video' or 'image'.

        Returns:
            A combined textual description of the media content.
        """
        if not Path(media_path).exists():
            logger.warning("Media file not found: %s", media_path)
            return ""

        if media_type == "video":
            return self._enrich_video(media_path)
        elif media_type == "image":
            return self._enrich_image(media_path)
        else:
            logger.warning("Unknown media type '%s' for %s", media_type, media_path)
            return ""

    def _enrich_video(self, video_path: str) -> str:
        """
        Process a video file:
          1. Extract audio → transcribe with faster-whisper (CPU)
          2. Extract key frames → describe with Moondream2 (GPU)
          3. Combine results into a textual summary
        """
        results = []

        # Step 1: Audio transcription (CPU — no VRAM impact)
        transcript = self._transcribe_audio(video_path)
        if transcript:
            results.append(f"[Audio transcript]: {transcript}")

        # Step 2: Frame extraction and visual description
        frames = self._extract_frames(video_path)
        if frames:
            # VRAM swap: unload LLM → load VLM → process → unload VLM → reload LLM
            self._swap_model_to_vision()
            for i, frame_path in enumerate(frames):
                description = self._describe_image_ollama(frame_path)
                if description:
                    results.append(f"[Frame {i + 1}]: {description}")
            self._swap_model_to_text()

            # Cleanup temporary frame files
            for fp in frames:
                try:
                    os.remove(fp)
                except OSError:
                    pass

        if not results:
            return "No media context could be extracted."

        return "\n".join(results)

    def _enrich_image(self, image_path: str) -> str:
        """Describe a single image using Moondream2."""
        self._swap_model_to_vision()
        description = self._describe_image_ollama(image_path)
        self._swap_model_to_text()
        return f"[Image]: {description}" if description else ""

    def _transcribe_audio(self, video_path: str) -> Optional[str]:
        """
        Extract audio from video and transcribe using faster-whisper on CPU.
        """
        model = self._get_whisper_model()
        if model is None:
            return None

        # Extract audio to a temporary WAV file using ffmpeg
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_audio = tmp.name

        try:
            cmd = [
                "ffmpeg", "-i", video_path,
                "-vn", "-acodec", "pcm_s16le",
                "-ar", "16000", "-ac", "1",
                "-y", tmp_audio,
            ]
            result = subprocess.run(
                cmd, capture_output=True, timeout=120, check=False,
            )
            if result.returncode != 0:
                logger.warning("ffmpeg audio extraction failed: %s", result.stderr.decode()[:200])
                return None

            segments, info = model.transcribe(tmp_audio, beam_size=3)
            transcript = " ".join(segment.text.strip() for segment in segments)
            logger.info(
                "Transcription complete: language=%s, duration=%.1fs, length=%d chars.",
                info.language,
                info.duration,
                len(transcript),
            )
            return transcript if transcript else None

        except subprocess.TimeoutExpired:
            logger.error("ffmpeg audio extraction timed out for %s", video_path)
            return None
        except Exception as exc:
            logger.error("Audio transcription error: %s", exc)
            return None
        finally:
            try:
                os.remove(tmp_audio)
            except OSError:
                pass

    def _extract_frames(self, video_path: str) -> list[str]:
        """
        Extract evenly-spaced key frames from a video using OpenCV.
        Returns a list of temporary file paths to the extracted frames.
        """
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                logger.warning("OpenCV failed to open video: %s", video_path)
                return []

            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = total_frames / fps

            if duration <= 0:
                cap.release()
                return []

            # Calculate frame positions (evenly spaced)
            interval = max(FRAME_EXTRACT_INTERVAL_SEC, duration / MAX_FRAMES_PER_VIDEO)
            timestamps = []
            t = interval / 2
            while t < duration and len(timestamps) < MAX_FRAMES_PER_VIDEO:
                timestamps.append(t)
                t += interval

            frame_paths = []
            for ts in timestamps:
                frame_number = int(ts * fps)
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                ret, frame = cap.read()
                if ret:
                    path = tempfile.mktemp(suffix=".jpg", prefix="orpheus_frame_")
                    cv2.imwrite(path, frame)
                    frame_paths.append(path)

            cap.release()
            logger.info(
                "Extracted %d frames from %s (duration=%.1fs).",
                len(frame_paths),
                video_path,
                duration,
            )
            return frame_paths

        except Exception as exc:
            logger.error("Frame extraction failed: %s", exc)
            return []

    def _describe_image_ollama(self, image_path: str) -> Optional[str]:
        """
        Send an image to the Ollama VLM (Moondream2) for description.
        """
        import base64

        try:
            with open(image_path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")

            with httpx.Client(timeout=60) as client:
                response = client.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": VISION_MODEL_NAME,
                        "prompt": "Describe what you see in this image in 2-3 sentences.",
                        "images": [image_b64],
                        "stream": False,
                    },
                )
                if response.status_code == 200:
                    return response.json().get("response", "")
                else:
                    logger.warning("Ollama VLM returned status %d.", response.status_code)
                    return None
        except Exception as exc:
            logger.error("Ollama VLM description error: %s", exc)
            return None

    def _swap_model_to_vision(self) -> None:
        """
        Trigger VRAM swap: signal Ollama to unload the text LLM
        and preload the vision model into GPU memory.
        """
        logger.info("VRAM swap: unloading LLM '%s', loading VLM '%s'...", TEXT_MODEL_NAME, VISION_MODEL_NAME)
        try:
            with httpx.Client(timeout=30) as client:
                # Unload text model by setting keep_alive to 0
                client.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": TEXT_MODEL_NAME,
                        "keep_alive": 0,
                    },
                )
                # Preload vision model
                client.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": VISION_MODEL_NAME,
                        "keep_alive": "5m",
                    },
                )
            logger.info("VRAM swap to VLM complete.")
        except Exception as exc:
            logger.warning("VRAM swap to VLM failed: %s — proceeding anyway.", exc)

    def _swap_model_to_text(self) -> None:
        """
        Trigger VRAM swap: unload the vision model and reload the text LLM.
        """
        logger.info("VRAM swap: unloading VLM '%s', reloading LLM '%s'...", VISION_MODEL_NAME, TEXT_MODEL_NAME)
        try:
            with httpx.Client(timeout=30) as client:
                # Unload vision model
                client.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": VISION_MODEL_NAME,
                        "keep_alive": 0,
                    },
                )
                # Preload text model (keep permanently)
                client.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": TEXT_MODEL_NAME,
                        "keep_alive": "-1",
                    },
                )
            logger.info("VRAM swap to LLM complete.")
        except Exception as exc:
            logger.warning("VRAM swap to LLM failed: %s — proceeding anyway.", exc)


# Module-level singleton
enricher = MediaEnricher()

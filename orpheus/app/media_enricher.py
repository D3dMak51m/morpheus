"""
ORPHEUS — Media Enricher
==========================
Handles extraction of frames/audio from video, local transcription
(faster-whisper on CPU), and VLM visual descriptions via Ollama (Moondream2).

Strict VRAM Scheduling:
Ensures VLM is loaded, queried, and immediately unloaded so
the main LLM has enough VRAM on the 6GB GTX 1660Ti constraint.
"""

import base64
import logging
import os
import subprocess
import tempfile
from typing import List, Optional

import cv2
import httpx

logger = logging.getLogger("orpheus.media_enricher")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
VISION_MODEL_NAME = os.getenv("VISION_MODEL_NAME", "moondream:latest")
# STT is centralized in the HEIMDALL service (faster-whisper) — ORPHEUS no longer
# loads its own Whisper (lighter image, faster startup); it just calls HEIMDALL when
# the legacy video path needs a transcript.
HEIMDALL_URL = os.getenv("HEIMDALL_URL", "http://heimdall:8004")
HEIMDALL_TIMEOUT = int(os.getenv("HEIMDALL_TIMEOUT_SEC", "180"))

class MediaEnricher:
    def __init__(self):
        logger.info("MediaEnricher ready (STT via HEIMDALL, VLM via Ollama).")

    def extract_audio(self, video_path: str) -> Optional[str]:
        """Extracts audio to a temporary WAV file using ffmpeg."""
        try:
            temp_audio = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            temp_audio.close()
            cmd = [
                "ffmpeg", "-y", "-i", video_path,
                "-q:a", "0", "-map", "a", temp_audio.name
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return temp_audio.name
        except subprocess.CalledProcessError:
            logger.warning("No audio track found or ffmpeg extraction failed for %s", video_path)
            return None

    def extract_keyframes(self, video_path: str, interval_sec: int = 3) -> List[str]:
        """Extracts 1 frame every interval_sec seconds using OpenCV."""
        frames = []
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error("Failed to open video: %s", video_path)
            return frames

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps == 0 or fps != fps: # Handle nan or 0
            fps = 30.0

        frame_interval = int(fps * interval_sec)
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % frame_interval == 0:
                temp_img = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                cv2.imwrite(temp_img.name, frame)
                frames.append(temp_img.name)
                temp_img.close()

            frame_idx += 1

        cap.release()
        return frames

    def transcribe_audio(self, audio_path: str) -> str:
        """Transcribe an audio file via the HEIMDALL STT service."""
        logger.info("Transcribing audio via HEIMDALL: %s", audio_path)
        try:
            with open(audio_path, "rb") as f:
                files = {"file": (os.path.basename(audio_path) or "audio", f, "application/octet-stream")}
                with httpx.Client(timeout=HEIMDALL_TIMEOUT) as client:
                    resp = client.post(f"{HEIMDALL_URL}/api/v1/transcribe", files=files)
                    resp.raise_for_status()
                    return (resp.json().get("text") or "").strip()
        except Exception as e:
            logger.error("HEIMDALL transcription failed: %s", e)
            return ""

    def query_vlm(self, image_path: str) -> str:
        """Sends an image to Ollama's Moondream VLM and unloads it immediately."""
        logger.info("Querying VLM for image: %s", image_path)
        try:
            with open(image_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode('utf-8')

            # Keep_alive: 0 forces unload after request to free VRAM
            payload = {
                "model": VISION_MODEL_NAME,
                "prompt": "Describe this image in detail. What objects, people, or text do you see?",
                "stream": False,
                "keep_alive": 0,
                "images": [img_b64]
            }

            with httpx.Client(timeout=60.0) as client:
                response = client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
                response.raise_for_status()
                return response.json().get("response", "").strip()

        except Exception as e:
            logger.error("VLM query failed: %s", e)
            return ""

    def process_media(self, media_path: str, media_type: str) -> str:
        """
        Orchestrates full media enrichment.
        Splits video, transcribes audio, queries VLM, and compiles a report.
        """
        report_sections = []
        temp_files = []

        try:
            if media_type == "video":
                logger.info("Processing video: %s", media_path)
                audio_path = self.extract_audio(media_path)
                if audio_path:
                    temp_files.append(audio_path)
                    transcript = self.transcribe_audio(audio_path)
                    if transcript:
                        report_sections.append(f"[Audio Transcript]\n{transcript}")

                frames = self.extract_keyframes(media_path)
                temp_files.extend(frames)
                
                visual_descriptions = []
                for idx, frame in enumerate(frames):
                    desc = self.query_vlm(frame)
                    if desc:
                        visual_descriptions.append(f"Frame {idx+1} (+{idx*3}s): {desc}")

                if visual_descriptions:
                    report_sections.append("[Visual Keyframes Description]\n" + "\n".join(visual_descriptions))

            elif media_type == "image":
                logger.info("Processing image: %s", media_path)
                desc = self.query_vlm(media_path)
                if desc:
                    report_sections.append(f"[Visual Description]\n{desc}")

        finally:
            # Cleanup temp files
            for tmp in temp_files:
                if os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            
            # Note: We do not delete the original raw_media file here yet, 
            # Orpheus main loop handles it after the event is fully processed.

        if not report_sections:
            return "No media content extracted."

        return "\n\n".join(report_sections)

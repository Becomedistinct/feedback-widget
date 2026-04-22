"""
Audio transcription — extracts audio from .webm and transcribes via faster-whisper.
"""
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    """Lazy-load the whisper model (downloads on first use, ~75MB for 'base')."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        logger.info("Loading Whisper model (base)...")
        _model = WhisperModel("base", device="cpu", compute_type="int8")
        logger.info("Whisper model loaded")
    return _model


def extract_audio(video_path: Path) -> Path | None:
    """Extract audio track from a .webm video file to .wav using ffmpeg."""
    audio_path = video_path.with_suffix(".wav")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-vn",                    # no video
                "-acodec", "pcm_s16le",   # PCM 16-bit
                "-ar", "16000",           # 16kHz sample rate
                "-ac", "1",               # mono
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning("ffmpeg failed: %s", result.stderr[:500])
            return None
        if not audio_path.exists() or audio_path.stat().st_size < 1000:
            logger.warning("ffmpeg produced empty or tiny audio file")
            return None
        return audio_path
    except FileNotFoundError:
        logger.warning("ffmpeg not installed")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg timed out")
        return None


def transcribe_audio(audio_path: Path) -> str | None:
    """Transcribe an audio file using faster-whisper (local, no API key needed)."""
    try:
        model = _get_model()
        segments, info = model.transcribe(
            str(audio_path),
            beam_size=5,
            language="en",
            vad_filter=True,  # Skip silent sections
        )

        # Collect all text segments
        texts = []
        for segment in segments:
            texts.append(segment.text.strip())

        transcript = " ".join(texts).strip()
        if not transcript:
            return None

        logger.info("Transcribed %d segments, %.0fs of speech", len(texts), info.duration)
        return transcript

    except Exception as e:
        logger.warning("Transcription failed: %s", e)
        return None


async def get_transcript(video_path: Path) -> str | None:
    """Full pipeline: extract audio from video, then transcribe."""
    audio_path = extract_audio(video_path)
    if not audio_path:
        logger.info("No audio extracted from video")
        return None

    try:
        transcript = transcribe_audio(audio_path)
        return transcript
    finally:
        # Clean up temporary audio file
        audio_path.unlink(missing_ok=True)

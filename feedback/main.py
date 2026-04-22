"""
Feedback Recorder — FastAPI backend
Receives screen recordings from the widget and creates Zoho Desk tickets
with MP4 video attachment and audio transcription.
"""
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from pathlib import Path
from dotenv import load_dotenv
import json
import subprocess
import uuid
import time
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).parent / ".env")

try:
    from feedback.zoho import create_ticket, attach_file
    from feedback.transcribe import get_transcript
except ImportError:
    from zoho import create_ticket, attach_file
    from transcribe import get_transcript

# Persistent storage: use /data if available (Railway volume), else local
DATA_DIR = Path("/data") if Path("/data").exists() else Path(__file__).parent
SUBMISSIONS_DIR = DATA_DIR / "submissions"
SUBMISSIONS_DIR.mkdir(exist_ok=True)

SITES_FILE = Path(__file__).parent / "sites.json"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB (Zoho attachment limit)

# Static dir: built widget JS lives here in production (Docker copies it to /app/static/)
STATIC_DIR = Path(__file__).parent.parent / "static"

# Base URL for building playback links (set via env or auto-detect)
BASE_URL = os.getenv("BASE_URL", "https://feedback-api-production-7e6f.up.railway.app")

app = FastAPI(title="Feedback Recorder API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/feedback-widget.js")
async def serve_widget():
    """Serve the built widget JS bundle."""
    js_path = STATIC_DIR / "feedback-widget.js"
    if not js_path.exists():
        raise HTTPException(404, "Widget not built yet")
    return FileResponse(js_path, media_type="application/javascript")


def load_sites() -> dict:
    """Load the site registry. Re-reads on each call so edits take effect without restart."""
    if SITES_FILE.exists():
        return json.loads(SITES_FILE.read_text()).get("sites", {})
    return {}


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/feedback/sites")
async def list_sites():
    """List all registered sites."""
    return load_sites()


@app.get("/api/feedback/recordings")
async def list_recordings():
    """List all submissions with metadata for redundancy/debugging."""
    recordings = []
    for site_dir in sorted(SUBMISSIONS_DIR.iterdir()):
        if not site_dir.is_dir():
            continue
        for meta_file in sorted(site_dir.glob("*.json"), reverse=True):
            try:
                meta = json.loads(meta_file.read_text())
                submission_id = meta["id"]
                mp4_path = site_dir / f"{submission_id}.mp4"
                webm_path = site_dir / f"{submission_id}.webm"
                meta["has_mp4"] = mp4_path.exists()
                meta["has_webm"] = webm_path.exists()
                meta["playback_url"] = f"{BASE_URL}/api/feedback/recordings/{submission_id}/view"
                recordings.append(meta)
            except Exception:
                continue
    return recordings


def _find_submission(submission_id: str) -> tuple[Path, dict] | None:
    """Return (site_dir, meta) for a submission, or None if not found."""
    for site_dir in SUBMISSIONS_DIR.iterdir():
        if not site_dir.is_dir():
            continue
        meta_path = site_dir / f"{submission_id}.json"
        if meta_path.exists():
            try:
                return site_dir, json.loads(meta_path.read_text())
            except Exception:
                pass
    return None


@app.get("/api/feedback/recordings/{submission_id}/view", response_class=HTMLResponse)
async def view_submission(submission_id: str):
    """Browser-friendly viewer: transcript + video or audio/screenshots."""
    result = _find_submission(submission_id)
    if not result:
        raise HTTPException(404, "Submission not found")
    site_dir, meta = result

    mode = meta.get("mode", "desktop")
    transcript = meta.get("transcript") or ""
    page_url = meta.get("page_url", "")
    client_name = meta.get("client_name", meta.get("site_id", ""))
    submitted = time.strftime("%B %d, %Y at %H:%M UTC", time.gmtime(meta.get("timestamp", 0)))

    transcript_html = f"<p>{transcript}</p>" if transcript else "<p><em>No speech detected.</em></p>"

    if mode == "mobile":
        # Find screenshots sorted by name
        ss_files = sorted(site_dir.glob(f"{submission_id}_ss_*.jpg"))
        screenshots_html = ""
        for i, _ in enumerate(ss_files):
            screenshots_html += (
                f'<a href="/api/feedback/recordings/{submission_id}/screenshot/{i}" target="_blank">'
                f'<img src="/api/feedback/recordings/{submission_id}/screenshot/{i}" '
                f'alt="Screenshot {i+1}" loading="lazy"></a>\n'
            )
        media_html = f"""
        <h2>Audio</h2>
        <audio controls src="/api/feedback/recordings/{submission_id}/audio" style="width:100%;margin-bottom:24px"></audio>
        <h2>Screenshots ({len(ss_files)})</h2>
        <div class="gallery">{screenshots_html or "<p><em>No screenshots captured.</em></p>"}</div>
        """
    else:
        mp4_path = site_dir / f"{submission_id}.mp4"
        webm_path = site_dir / f"{submission_id}.webm"
        media_url = f"/api/feedback/recordings/{submission_id}/media"
        if mp4_path.exists() or webm_path.exists():
            media_html = f"""
            <h2>Recording</h2>
            <video controls src="{media_url}" style="width:100%;border-radius:8px;margin-bottom:24px" preload="metadata"></video>
            """
        else:
            media_html = "<p><em>Recording file not available.</em></p>"

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Feedback from {client_name}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 800px; margin: 0 auto; padding: 24px 16px; color: #111; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .meta {{ font-size: 13px; color: #666; margin-bottom: 32px; }}
  .meta a {{ color: #2563eb; }}
  h2 {{ font-size: 16px; font-weight: 600; margin: 24px 0 8px; }}
  p {{ line-height: 1.6; color: #333; }}
  .gallery {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; }}
  .gallery img {{ width: 100%; border-radius: 6px; border: 1px solid #e5e7eb; cursor: zoom-in; }}
</style>
</head>
<body>
<h1>Feedback — {client_name}</h1>
<div class="meta">Submitted {submitted} &nbsp;·&nbsp; <a href="{page_url}" target="_blank">{page_url}</a></div>
<h2>What they said</h2>
{transcript_html}
{media_html}
</body>
</html>""")


@app.get("/api/feedback/recordings/{submission_id}/media")
async def serve_media(submission_id: str):
    """Serve the video file inline (desktop recordings)."""
    result = _find_submission(submission_id)
    if not result:
        raise HTTPException(404, "Not found")
    site_dir, _ = result
    mp4_path = site_dir / f"{submission_id}.mp4"
    if mp4_path.exists():
        return FileResponse(mp4_path, media_type="video/mp4")
    webm_path = site_dir / f"{submission_id}.webm"
    if webm_path.exists():
        return FileResponse(webm_path, media_type="video/webm")
    raise HTTPException(404, "Media file not found")


@app.get("/api/feedback/recordings/{submission_id}/audio")
async def serve_audio(submission_id: str):
    """Serve the audio file inline (mobile recordings)."""
    result = _find_submission(submission_id)
    if not result:
        raise HTTPException(404, "Not found")
    site_dir, _ = result
    webm_path = site_dir / f"{submission_id}.webm"
    if webm_path.exists():
        return FileResponse(webm_path, media_type="audio/webm")
    raise HTTPException(404, "Audio file not found")


@app.get("/api/feedback/recordings/{submission_id}/screenshot/{index}")
async def serve_screenshot(submission_id: str, index: int):
    """Serve a screenshot image inline."""
    result = _find_submission(submission_id)
    if not result:
        raise HTTPException(404, "Not found")
    site_dir, _ = result
    ss_files = sorted(site_dir.glob(f"{submission_id}_ss_*.jpg"))
    if index < 0 or index >= len(ss_files):
        raise HTTPException(404, "Screenshot not found")
    return FileResponse(ss_files[index], media_type="image/jpeg")


@app.get("/api/feedback/recordings/{submission_id}")
async def serve_recording(submission_id: str):
    """Legacy: serve raw recording file (redirects to viewer for browser, serves file for direct access)."""
    result = _find_submission(submission_id)
    if not result:
        raise HTTPException(404, "Recording not found")
    site_dir, meta = result
    mp4_path = site_dir / f"{submission_id}.mp4"
    if mp4_path.exists():
        return FileResponse(mp4_path, media_type="video/mp4")
    webm_path = site_dir / f"{submission_id}.webm"
    if webm_path.exists():
        mime = "audio/webm" if meta.get("mode") == "mobile" else "video/webm"
        return FileResponse(webm_path, media_type=mime)
    raise HTTPException(404, "Recording not found")


def convert_to_mp4(webm_path: Path) -> Path | None:
    """Convert .webm to .mp4 (H.264+AAC) using ffmpeg."""
    mp4_path = webm_path.with_suffix(".mp4")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(webm_path),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",  # Enable streaming playback
                str(mp4_path),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.warning("MP4 conversion failed: %s", result.stderr[:500])
            return None
        if not mp4_path.exists() or mp4_path.stat().st_size == 0:
            logger.warning("MP4 conversion produced empty file")
            return None
        logger.info("Converted to MP4: %.1f MB", mp4_path.stat().st_size / 1024 / 1024)
        return mp4_path
    except FileNotFoundError:
        logger.warning("ffmpeg not installed, skipping MP4 conversion")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg MP4 conversion timed out")
        mp4_path.unlink(missing_ok=True)
        return None


def build_ticket_description(
    client_name: str,
    site_id: str,
    page_url: str,
    user_agent: str,
    total_size: int,
    submission_id: str,
    transcript: str | None,
    mode: str = "desktop",
    screenshot_count: int = 0,
) -> str:
    """Build a clean HTML ticket description for Zoho Desk."""
    playback_url = f"{BASE_URL}/api/feedback/recordings/{submission_id}/view"
    submitted_at = time.strftime("%B %d, %Y at %H:%M UTC", time.gmtime())

    transcript_html = (
        f"<p>{transcript}</p>"
        if transcript
        else "<p><em>No speech detected in recording.</em></p>"
    )

    recording_section = (
        f'<p><a href="{playback_url}">▶ Watch Recording</a></p>'
        if mode == "desktop"
        else f"<p>{screenshot_count} screenshots attached below.</p>"
    )

    size_str = (
        f"{total_size / 1024 / 1024:.1f} MB"
        if total_size > 1024 * 1024
        else f"{total_size / 1024:.0f} KB"
    )

    return f"""<h2>What they said</h2>
{transcript_html}

<h2>Recording</h2>
{recording_section}

<h2>Details</h2>
<table>
  <tr><td><strong>Page</strong></td><td><a href="{page_url}">{page_url}</a></td></tr>
  <tr><td><strong>Client</strong></td><td>{client_name}</td></tr>
  <tr><td><strong>Submitted</strong></td><td>{submitted_at}</td></tr>
  <tr><td><strong>File size</strong></td><td>{size_str}</td></tr>
  <tr><td><strong>Submission ID</strong></td><td>{submission_id}</td></tr>
</table>"""


@app.post("/api/feedback/submit")
async def submit_feedback(
    video: UploadFile = File(...),
    site_id: str = Form(...),
    page_url: str = Form(""),
    user_agent: str = Form(""),
):
    # Look up site config for client email
    sites = load_sites()
    site_config = sites.get(site_id)
    if not site_config:
        raise HTTPException(400, f"Unknown site_id: {site_id}. Register it in sites.json first.")

    client_email = site_config["client_email"]
    client_name = site_config.get("client_name", site_id)

    submission_id = str(uuid.uuid4())
    site_dir = SUBMISSIONS_DIR / site_id
    site_dir.mkdir(exist_ok=True)

    # Save video file with size check
    video_path = site_dir / f"{submission_id}.webm"
    total_size = 0
    with open(video_path, "wb") as f:
        while chunk := await video.read(1024 * 1024):
            total_size += len(chunk)
            if total_size > MAX_FILE_SIZE:
                video_path.unlink(missing_ok=True)
                raise HTTPException(413, "File too large (max 50MB)")
            f.write(chunk)

    logger.info("Saved recording: %s (%.1f MB)", submission_id, total_size / 1024 / 1024)

    # Convert .webm to .mp4 for universal playback
    mp4_path = convert_to_mp4(video_path)
    attach_path = mp4_path if mp4_path else video_path

    # Transcribe the audio from the recording
    transcript = None
    try:
        transcript = await get_transcript(video_path)
        if transcript:
            logger.info("Transcription complete: %d chars", len(transcript))
        else:
            logger.info("No speech detected in recording")
    except Exception as e:
        logger.warning("Transcription failed: %s", e)

    # Save metadata (including transcript)
    meta = {
        "id": submission_id,
        "site_id": site_id,
        "page_url": page_url,
        "user_agent": user_agent,
        "client_email": client_email,
        "client_name": client_name,
        "timestamp": time.time(),
        "file_size_bytes": total_size,
        "transcript": transcript,
        "has_mp4": mp4_path is not None,
    }
    meta_path = site_dir / f"{submission_id}.json"
    meta_path.write_text(json.dumps(meta, indent=2))

    # Build ticket description
    description = build_ticket_description(
        client_name=client_name,
        site_id=site_id,
        page_url=page_url,
        user_agent=user_agent,
        total_size=total_size,
        submission_id=submission_id,
        transcript=transcript,
        mode="desktop",
    )

    # Create Zoho Desk ticket with attachment
    ticket_id = None
    zoho_error = None
    try:
        ticket_id = await create_ticket(
            subject=f"Screen Feedback — {client_name}",
            description=description,
            contact_email=client_email,
        )
        logger.info("Created Zoho ticket: %s", ticket_id)
    except Exception as e:
        logger.error("Zoho ticket creation failed: %s", e)
        zoho_error = f"Ticket creation: {e}"

    # Attach video file separately (so ticket exists even if attachment fails)
    if ticket_id:
        try:
            await attach_file(ticket_id, attach_path)
            logger.info("Attached %s to ticket %s", attach_path.suffix, ticket_id)
        except Exception as e:
            logger.error("Zoho attachment failed: %s", e)
            zoho_error = f"Attachment: {e}"

    return JSONResponse({
        "submission_id": submission_id,
        "ticket_id": ticket_id,
        "transcript": transcript[:200] if transcript else None,
        "zoho_error": zoho_error,
    })


@app.post("/api/feedback/submit-mobile")
async def submit_mobile_feedback(
    request: Request,
    audio: UploadFile = File(...),
    site_id: str = Form(...),
    page_url: str = Form(""),
    user_agent: str = Form(""),
):
    """Mobile feedback: audio recording + page screenshots."""
    # Look up site config
    sites = load_sites()
    site_config = sites.get(site_id)
    if not site_config:
        raise HTTPException(400, f"Unknown site_id: {site_id}. Register it in sites.json first.")

    client_email = site_config["client_email"]
    client_name = site_config.get("client_name", site_id)

    submission_id = str(uuid.uuid4())
    site_dir = SUBMISSIONS_DIR / site_id
    site_dir.mkdir(exist_ok=True)

    # Save audio file
    audio_path = site_dir / f"{submission_id}.webm"
    total_size = 0
    with open(audio_path, "wb") as f:
        while chunk := await audio.read(1024 * 1024):
            total_size += len(chunk)
            if total_size > MAX_FILE_SIZE:
                audio_path.unlink(missing_ok=True)
                raise HTTPException(413, "File too large (max 50MB)")
            f.write(chunk)

    logger.info("Saved mobile audio: %s (%.1f KB)", submission_id, total_size / 1024)

    # Save screenshots from multipart form
    form = await request.form()
    screenshot_paths = []
    for i, (key, field) in enumerate(form.multi_items()):
        if key == "screenshots" and hasattr(field, "read"):
            ss_path = site_dir / f"{submission_id}_ss_{i:03d}.jpg"
            content = await field.read()
            ss_path.write_bytes(content)
            screenshot_paths.append(ss_path)
            logger.info("Saved screenshot %d: %.1f KB", i, len(content) / 1024)

    logger.info("Mobile submission %s: %d screenshots", submission_id, len(screenshot_paths))

    # Transcribe audio
    transcript = None
    try:
        transcript = await get_transcript(audio_path)
        if transcript:
            logger.info("Transcription complete: %d chars", len(transcript))
        else:
            logger.info("No speech detected in audio")
    except Exception as e:
        logger.warning("Transcription failed: %s", e)

    # Save metadata
    meta = {
        "id": submission_id,
        "site_id": site_id,
        "page_url": page_url,
        "user_agent": user_agent,
        "client_email": client_email,
        "client_name": client_name,
        "timestamp": time.time(),
        "file_size_bytes": total_size,
        "transcript": transcript,
        "mode": "mobile",
        "screenshot_count": len(screenshot_paths),
    }
    meta_path = site_dir / f"{submission_id}.json"
    meta_path.write_text(json.dumps(meta, indent=2))

    # Build ticket description
    description = build_ticket_description(
        client_name=client_name,
        site_id=site_id,
        page_url=page_url,
        user_agent=user_agent,
        total_size=total_size,
        submission_id=submission_id,
        transcript=transcript,
        mode="mobile",
        screenshot_count=len(screenshot_paths),
    )

    # Create Zoho Desk ticket
    ticket_id = None
    zoho_error = None
    try:
        ticket_id = await create_ticket(
            subject=f"Mobile Feedback — {client_name}",
            description=description,
            contact_email=client_email,
        )
        logger.info("Created Zoho ticket: %s", ticket_id)
    except Exception as e:
        logger.error("Zoho ticket creation failed: %s", e)
        zoho_error = f"Ticket creation: {e}"

    # Attach screenshots to ticket
    if ticket_id:
        for ss_path in screenshot_paths:
            try:
                await attach_file(ticket_id, ss_path)
                logger.info("Attached screenshot %s to ticket %s", ss_path.name, ticket_id)
            except Exception as e:
                logger.error("Screenshot attachment failed: %s", e)
                if not zoho_error:
                    zoho_error = f"Screenshot attachment: {e}"

    return JSONResponse({
        "submission_id": submission_id,
        "ticket_id": ticket_id,
        "transcript": transcript[:200] if transcript else None,
        "zoho_error": zoho_error,
    })

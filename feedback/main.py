"""
Feedback Recorder — FastAPI backend
Receives screen recordings from the widget and creates Zoho Desk tickets
with MP4 video attachment and audio transcription.
"""
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends
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
import secrets
import logging
import bcrypt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).parent / ".env")

import asyncio
import resend

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

# sites.json lives on the volume so admin changes survive redeploys
SITES_FILE = DATA_DIR / "sites.json"
_bundled_sites = Path(__file__).parent / "sites.json"
if not SITES_FILE.exists() and _bundled_sites.exists():
    import shutil
    shutil.copy(_bundled_sites, SITES_FILE)
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
    return FileResponse(
        js_path,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


ADMIN_KEY = os.getenv("ADMIN_KEY", "")

# users.json stores {username: bcrypt_hash} on the Railway volume
USERS_FILE = DATA_DIR / "users.json"

# invites.json stores {token: {username, email, created_at}} on the volume
INVITES_FILE = DATA_DIR / "invites.json"
INVITE_EXPIRY_SECONDS = 7 * 24 * 3600  # 7 days

# In-memory session tokens: {token: username} — cleared on restart
_sessions: dict = {}


def load_users() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text())
    return {}


def save_users(users: dict) -> None:
    USERS_FILE.write_text(json.dumps(users, indent=2))


def _user_hash(entry) -> str | None:
    """Return bcrypt hash from a user entry (str = legacy, dict = current)."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("hash")
    return None


def _user_email(entry) -> str | None:
    if isinstance(entry, dict):
        return entry.get("email")
    return None


def load_invites() -> dict:
    if INVITES_FILE.exists():
        return json.loads(INVITES_FILE.read_text())
    return {}


def save_invites(invites: dict) -> None:
    INVITES_FILE.write_text(json.dumps(invites, indent=2))


# resets.json stores {token: {username, created_at}}
RESETS_FILE = DATA_DIR / "resets.json"
RESET_EXPIRY_SECONDS = 2 * 3600  # 2 hours


def load_resets() -> dict:
    if RESETS_FILE.exists():
        return json.loads(RESETS_FILE.read_text())
    return {}


def save_resets(resets: dict) -> None:
    RESETS_FILE.write_text(json.dumps(resets, indent=2))


def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = username
    return token


def _is_valid_key(key: str) -> bool:
    if ADMIN_KEY and key == ADMIN_KEY:
        return True
    return key in _sessions


def require_admin(request: Request):
    key = request.headers.get("X-Admin-Key") or request.query_params.get("key", "")
    if not key or not _is_valid_key(key):
        raise HTTPException(403, "Invalid or missing admin key")


def load_sites() -> dict:
    if SITES_FILE.exists():
        return json.loads(SITES_FILE.read_text()).get("sites", {})
    return {}


def save_sites(sites: dict) -> None:
    SITES_FILE.write_text(json.dumps({"sites": sites}, indent=2))


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/feedback/sites")
async def list_sites_public():
    return load_sites()


@app.post("/api/admin/login")
async def admin_login(request: Request):
    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", "")).strip()
    if not password:
        raise HTTPException(403, "Please enter your password")
    # Master ADMIN_KEY: no username required
    if ADMIN_KEY and password == ADMIN_KEY:
        token = create_session(username or "admin")
        return {"ok": True, "token": token, "username": username or "admin"}
    # Volume users: require username for bcrypt check
    if not username:
        raise HTTPException(403, "Please enter your username")
    users = load_users()
    hashed = _user_hash(users.get(username))
    if hashed and bcrypt.checkpw(password.encode(), hashed.encode()):
        token = create_session(username)
        return {"ok": True, "token": token, "username": username}
    raise HTTPException(403, "Incorrect username or password")


@app.get("/api/admin/users", dependencies=[Depends(require_admin)])
async def admin_list_users():
    return {"users": list(load_users().keys())}


@app.post("/api/admin/invite", dependencies=[Depends(require_admin)])
async def admin_invite_user(request: Request):
    body = await request.json()
    username = str(body.get("username", "")).strip()
    email = str(body.get("email", "")).strip()
    if not username or not email:
        raise HTTPException(400, "username and email are required")
    users = load_users()
    if username in users:
        raise HTTPException(409, "a user with that username already exists")
    # Invalidate any existing pending invite for this username
    invites = load_invites()
    invites = {t: v for t, v in invites.items() if v.get("username") != username}
    token = secrets.token_urlsafe(32)
    invites[token] = {"username": username, "email": email, "created_at": time.time()}
    save_invites(invites)
    invite_url = f"{BASE_URL}/admin/accept-invite?token={token}"
    html = f"""
    <p>Hi {username},</p>
    <p>You've been invited to the Feedback Widget admin panel.</p>
    <p><a href="{invite_url}">Click here to create your account</a></p>
    <p>This link expires in 7 days.</p>
    <p style="color:#9ca3af;font-size:12px">If you weren't expecting this, you can ignore it.</p>
    """
    await _send_email("You've been invited to Feedback Widget Admin", html, to=email)
    return {"ok": True}


@app.get("/api/admin/invites", dependencies=[Depends(require_admin)])
async def admin_list_invites():
    invites = load_invites()
    now = time.time()
    active = [
        {"token": t[:8] + "…", "username": v["username"], "email": v["email"],
         "expires_in_hours": round((v["created_at"] + INVITE_EXPIRY_SECONDS - now) / 3600, 1)}
        for t, v in invites.items()
        if now < v["created_at"] + INVITE_EXPIRY_SECONDS
    ]
    return {"invites": active}


@app.delete("/api/admin/invites/{username}", dependencies=[Depends(require_admin)])
async def admin_revoke_invite(username: str):
    invites = load_invites()
    before = len(invites)
    invites = {t: v for t, v in invites.items() if v.get("username") != username}
    if len(invites) == before:
        raise HTTPException(404, "no pending invite for that username")
    save_invites(invites)
    return {"ok": True}


@app.delete("/api/admin/users/{username}", dependencies=[Depends(require_admin)])
async def admin_delete_user(username: str):
    users = load_users()
    if username not in users:
        raise HTTPException(404, "user not found")
    del users[username]
    save_users(users)
    return {"ok": True}


@app.post("/api/admin/users/{username}/reset", dependencies=[Depends(require_admin)])
async def admin_trigger_reset(username: str):
    users = load_users()
    entry = users.get(username)
    if entry is None:
        raise HTTPException(404, "user not found")
    email = _user_email(entry)
    if not email:
        raise HTTPException(400, "no email on file for this user — remove and re-invite them")
    # Invalidate any existing reset for this user
    resets = load_resets()
    resets = {t: v for t, v in resets.items() if v.get("username") != username}
    token = secrets.token_urlsafe(32)
    resets[token] = {"username": username, "created_at": time.time()}
    save_resets(resets)
    reset_url = f"{BASE_URL}/admin/reset-password?token={token}"
    html = f"""
    <p>Hi {username},</p>
    <p>A password reset was requested for your Feedback Widget admin account.</p>
    <p><a href="{reset_url}">Click here to set a new password</a></p>
    <p>This link expires in 2 hours. If you didn't request this, you can ignore it.</p>
    """
    await _send_email("Reset your Feedback Widget password", html, to=email)
    return {"ok": True}


@app.get("/admin/forgot-password", response_class=HTMLResponse)
async def forgot_password_page():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Forgot password</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f9fafb;display:flex;align-items:center;justify-content:center;min-height:100vh}
  .card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:36px;width:100%;max-width:360px}
  h1{font-size:20px;font-weight:700;margin-bottom:6px;color:#111}
  .sub{font-size:14px;color:#6b7280;margin-bottom:24px;line-height:1.5}
  label{display:block;font-size:12px;font-weight:500;color:#374151;margin-bottom:4px}
  input{width:100%;padding:9px 11px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;font-family:inherit;margin-bottom:14px}
  input:focus{outline:none;border-color:#2563eb;box-shadow:0 0 0 2px rgba(37,99,235,.15)}
  .btn{width:100%;padding:11px;background:#2563eb;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:500;cursor:pointer;font-family:inherit}
  .btn:hover{background:#1d4ed8} .btn:disabled{background:#93c5fd;cursor:default}
  .back{display:block;text-align:center;margin-top:16px;font-size:13px;color:#6b7280;text-decoration:none}
  .back:hover{color:#111}
  #msg{font-size:13px;margin-top:12px;text-align:center}
  .err{color:#dc2626} .ok{color:#059669}
</style>
</head>
<body>
<div class="card">
  <h1>Forgot your password?</h1>
  <p class="sub">Enter your username and we'll email you a link to reset your password.</p>
  <label>Username</label>
  <input type="text" id="username" placeholder="Your username" autocomplete="username">
  <button class="btn" id="submit-btn" onclick="submit()">Send reset link</button>
  <div id="msg"></div>
  <a href="/admin" class="back">Back to sign in</a>
</div>
<script>
function submit() {
  const username = document.getElementById('username').value.trim();
  const msg = document.getElementById('msg');
  const btn = document.getElementById('submit-btn');
  msg.textContent = '';
  if (!username) { msg.className = 'err'; msg.textContent = 'Please enter your username.'; return; }
  btn.disabled = true; btn.textContent = 'Sending…';
  fetch('/api/admin/forgot-password', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username})
  })
  .then(r => r.json())
  .then(() => {
    msg.className = 'ok';
    msg.textContent = 'If we have an email on file for that username, a reset link has been sent.';
    btn.disabled = false; btn.textContent = 'Send reset link';
    document.getElementById('username').value = '';
  })
  .catch(() => {
    msg.className = 'err'; msg.textContent = 'Something went wrong. Please try again.';
    btn.disabled = false; btn.textContent = 'Send reset link';
  });
}
document.getElementById('username').addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
</script>
</body>
</html>""")


@app.post("/api/admin/forgot-password")
async def forgot_password(request: Request):
    body = await request.json()
    username = str(body.get("username", "")).strip()
    if username:
        users = load_users()
        entry = users.get(username)
        if entry:
            email = _user_email(entry)
            if email:
                resets = load_resets()
                resets = {t: v for t, v in resets.items() if v.get("username") != username}
                token = secrets.token_urlsafe(32)
                resets[token] = {"username": username, "created_at": time.time()}
                save_resets(resets)
                reset_url = f"{BASE_URL}/admin/reset-password?token={token}"
                html = f"""
                <p>Hi {username},</p>
                <p>A password reset was requested for your Feedback Widget admin account.</p>
                <p><a href="{reset_url}">Click here to set a new password</a></p>
                <p>This link expires in 2 hours.</p>
                <p style="color:#9ca3af;font-size:12px">If you didn't request this, you can ignore it.</p>
                """
                await _send_email("Reset your Feedback Widget password", html, to=email)
    # Always return the same response to avoid username enumeration
    return {"ok": True}


@app.get("/admin/reset-password", response_class=HTMLResponse)
async def reset_password_page(token: str = ""):
    if not token:
        return HTMLResponse("<p>Invalid link.</p>", status_code=400)
    resets = load_resets()
    reset = resets.get(token)
    if not reset or time.time() > reset["created_at"] + RESET_EXPIRY_SECONDS:
        return HTMLResponse("""<!DOCTYPE html><html><head><meta charset="UTF-8">
        <title>Link Expired</title>
        <style>body{font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f9fafb}
        .box{text-align:center;padding:40px;background:#fff;border-radius:12px;border:1px solid #e5e7eb;max-width:360px}
        h2{font-size:18px;margin-bottom:8px;color:#111} p{color:#6b7280;font-size:14px}</style>
        </head><body><div class="box"><h2>Link expired or invalid</h2>
        <p>Ask your admin to send a new password reset link.</p></div></body></html>""", status_code=410)
    username = reset["username"]
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reset password</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f9fafb;display:flex;align-items:center;justify-content:center;min-height:100vh}}
  .card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:36px;width:100%;max-width:360px}}
  h1{{font-size:20px;font-weight:700;margin-bottom:6px;color:#111}}
  .sub{{font-size:14px;color:#6b7280;margin-bottom:24px}}
  label{{display:block;font-size:12px;font-weight:500;color:#374151;margin-bottom:4px}}
  input{{width:100%;padding:9px 11px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;font-family:inherit;margin-bottom:14px}}
  input:focus{{outline:none;border-color:#2563eb;box-shadow:0 0 0 2px rgba(37,99,235,.15)}}
  .btn{{width:100%;padding:11px;background:#2563eb;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:500;cursor:pointer;font-family:inherit}}
  .btn:hover{{background:#1d4ed8}} .btn:disabled{{background:#93c5fd;cursor:default}}
  #msg{{font-size:13px;margin-top:12px;text-align:center}}
  .err{{color:#dc2626}} .ok{{color:#059669}}
</style>
</head>
<body>
<div class="card">
  <h1>Reset your password</h1>
  <p class="sub">Resetting password for <strong>{username}</strong>.</p>
  <label>New password</label>
  <input type="password" id="pw1" placeholder="At least 8 characters" autocomplete="new-password">
  <label>Confirm new password</label>
  <input type="password" id="pw2" placeholder="Re-enter password" autocomplete="new-password">
  <button class="btn" id="submit-btn" onclick="submit()">Set new password</button>
  <div id="msg"></div>
</div>
<script>
function submit() {{
  const pw1 = document.getElementById('pw1').value;
  const pw2 = document.getElementById('pw2').value;
  const msg = document.getElementById('msg');
  const btn = document.getElementById('submit-btn');
  msg.textContent = '';
  if (pw1.length < 8) {{ msg.className = 'err'; msg.textContent = 'Password must be at least 8 characters.'; return; }}
  if (pw1 !== pw2) {{ msg.className = 'err'; msg.textContent = 'Passwords do not match.'; return; }}
  btn.disabled = true; btn.textContent = 'Saving…';
  fetch('/api/admin/reset/{token}/confirm', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{password: pw1}})
  }})
  .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e.detail)))
  .then(() => {{
    msg.className = 'ok';
    msg.textContent = 'Password updated! Redirecting to sign in…';
    setTimeout(() => window.location.href = '/admin', 1500);
  }})
  .catch(err => {{
    btn.disabled = false; btn.textContent = 'Set new password';
    msg.className = 'err'; msg.textContent = err || 'Something went wrong.';
  }});
}}
['pw1','pw2'].forEach(id => document.getElementById(id).addEventListener('keydown', e => {{ if (e.key === 'Enter') submit(); }}));
</script>
</body>
</html>""")


@app.post("/api/admin/reset/{token}/confirm")
async def confirm_reset(token: str, request: Request):
    resets = load_resets()
    reset = resets.get(token)
    if not reset or time.time() > reset["created_at"] + RESET_EXPIRY_SECONDS:
        raise HTTPException(410, "Reset link has expired or is invalid")
    body = await request.json()
    password = str(body.get("password", "")).strip()
    if len(password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    username = reset["username"]
    users = load_users()
    if username not in users:
        raise HTTPException(404, "user not found")
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    entry = users[username]
    if isinstance(entry, dict):
        entry["hash"] = hashed
    else:
        users[username] = hashed  # preserve legacy format
    save_users(users)
    del resets[token]
    save_resets(resets)
    return {"ok": True}


@app.get("/admin/accept-invite", response_class=HTMLResponse)
async def accept_invite_page(token: str = ""):
    if not token:
        return HTMLResponse("<p>Invalid link.</p>", status_code=400)
    invites = load_invites()
    invite = invites.get(token)
    if not invite or time.time() > invite["created_at"] + INVITE_EXPIRY_SECONDS:
        return HTMLResponse("""<!DOCTYPE html><html><head><meta charset="UTF-8">
        <title>Link Expired</title>
        <style>body{font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f9fafb}
        .box{text-align:center;padding:40px;background:#fff;border-radius:12px;border:1px solid #e5e7eb;max-width:360px}
        h2{font-size:18px;margin-bottom:8px;color:#111} p{color:#6b7280;font-size:14px}</style>
        </head><body><div class="box"><h2>Link expired or invalid</h2>
        <p>Ask your admin to send a new invite.</p></div></body></html>""", status_code=410)
    username = invite["username"]
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Create your account</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f9fafb;display:flex;align-items:center;justify-content:center;min-height:100vh}}
  .card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:36px;width:100%;max-width:360px}}
  h1{{font-size:20px;font-weight:700;margin-bottom:6px;color:#111}}
  .sub{{font-size:14px;color:#6b7280;margin-bottom:24px}}
  label{{display:block;font-size:12px;font-weight:500;color:#374151;margin-bottom:4px}}
  input{{width:100%;padding:9px 11px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;font-family:inherit;margin-bottom:14px}}
  input:focus{{outline:none;border-color:#2563eb;box-shadow:0 0 0 2px rgba(37,99,235,.15)}}
  .btn{{width:100%;padding:11px;background:#2563eb;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:500;cursor:pointer;font-family:inherit}}
  .btn:hover{{background:#1d4ed8}}
  .btn:disabled{{background:#93c5fd;cursor:default}}
  #msg{{font-size:13px;margin-top:12px;text-align:center}}
  .err{{color:#dc2626}} .ok{{color:#059669}}
</style>
</head>
<body>
<div class="card">
  <h1>Create your account</h1>
  <p class="sub">Welcome, <strong>{username}</strong>. Set a password to get started.</p>
  <label>Password</label>
  <input type="password" id="pw1" placeholder="At least 8 characters" autocomplete="new-password">
  <label>Confirm password</label>
  <input type="password" id="pw2" placeholder="Re-enter password" autocomplete="new-password">
  <button class="btn" id="submit-btn" onclick="submit()">Create account</button>
  <div id="msg"></div>
</div>
<script>
function submit() {{
  const pw1 = document.getElementById('pw1').value;
  const pw2 = document.getElementById('pw2').value;
  const msg = document.getElementById('msg');
  const btn = document.getElementById('submit-btn');
  msg.textContent = '';
  if (pw1.length < 8) {{ msg.className = 'err'; msg.textContent = 'Password must be at least 8 characters.'; return; }}
  if (pw1 !== pw2) {{ msg.className = 'err'; msg.textContent = 'Passwords do not match.'; return; }}
  btn.disabled = true;
  btn.textContent = 'Creating account…';
  fetch('/api/admin/invite/{token}/accept', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{password: pw1}})
  }})
  .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e.detail)))
  .then(() => {{
    msg.className = 'ok';
    msg.textContent = 'Account created! Redirecting to sign in…';
    setTimeout(() => window.location.href = '/admin', 1500);
  }})
  .catch(err => {{
    btn.disabled = false;
    btn.textContent = 'Create account';
    msg.className = 'err';
    msg.textContent = err || 'Something went wrong.';
  }});
}}
['pw1','pw2'].forEach(id => document.getElementById(id).addEventListener('keydown', e => {{ if (e.key === 'Enter') submit(); }}));
</script>
</body>
</html>""")


@app.post("/api/admin/invite/{token}/accept")
async def accept_invite(token: str, request: Request):
    invites = load_invites()
    invite = invites.get(token)
    if not invite or time.time() > invite["created_at"] + INVITE_EXPIRY_SECONDS:
        raise HTTPException(410, "Invite link has expired or is invalid")
    body = await request.json()
    password = str(body.get("password", "")).strip()
    if len(password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    username = invite["username"]
    users = load_users()
    if username in users:
        raise HTTPException(409, "account already exists — contact your admin")
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    users[username] = {"hash": hashed, "email": invite["email"]}
    save_users(users)
    # Remove the used invite
    del invites[token]
    save_invites(invites)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin: sites CRUD
# ---------------------------------------------------------------------------

@app.get("/api/admin/sites", dependencies=[Depends(require_admin)])
async def admin_list_sites():
    return load_sites()


@app.post("/api/admin/sites", dependencies=[Depends(require_admin)])
async def admin_create_site(
    site_id: str = Form(...),
    client_name: str = Form(...),
    client_email: str = Form(...),
):
    sites = load_sites()
    if site_id in sites:
        raise HTTPException(400, f"Site '{site_id}' already exists")
    sites[site_id] = {"client_name": client_name, "client_email": client_email}
    save_sites(sites)
    return {"site_id": site_id, **sites[site_id]}


@app.put("/api/admin/sites/{site_id}", dependencies=[Depends(require_admin)])
async def admin_update_site(
    site_id: str,
    client_name: str = Form(...),
    client_email: str = Form(...),
):
    sites = load_sites()
    if site_id not in sites:
        raise HTTPException(404, f"Site '{site_id}' not found")
    sites[site_id] = {"client_name": client_name, "client_email": client_email}
    save_sites(sites)
    return {"site_id": site_id, **sites[site_id]}


@app.delete("/api/admin/sites/{site_id}", dependencies=[Depends(require_admin)])
async def admin_delete_site(site_id: str):
    sites = load_sites()
    if site_id not in sites:
        raise HTTPException(404, f"Site '{site_id}' not found")
    del sites[site_id]
    save_sites(sites)
    return {"deleted": site_id}


# ---------------------------------------------------------------------------
# Admin UI
# ---------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
async def admin_ui():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Feedback Widget — Admin</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f9fafb;color:#111827;padding:32px 16px}
  .wrap{max-width:720px;margin:0 auto}
  h1{font-size:22px;font-weight:700;margin-bottom:4px}
  .sub{color:#6b7280;font-size:14px;margin-bottom:32px}
  .card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:24px;margin-bottom:24px}
  h2{font-size:15px;font-weight:600;margin-bottom:16px}
  table{width:100%;border-collapse:collapse;font-size:14px}
  th{text-align:left;padding:8px 10px;background:#f3f4f6;color:#6b7280;font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
  td{padding:10px 10px;border-top:1px solid #f3f4f6;vertical-align:middle}
  .actions{display:flex;gap:8px}
  input{width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;font-family:inherit}
  input:focus{outline:none;border-color:#2563eb;box-shadow:0 0 0 2px rgba(37,99,235,.15)}
  .btn{padding:7px 14px;border:none;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;font-family:inherit}
  .btn-primary{background:#2563eb;color:#fff}.btn-primary:hover{background:#1d4ed8}
  .btn-secondary{background:#f3f4f6;color:#374151;border:1px solid #d1d5db}.btn-secondary:hover{background:#e5e7eb}
  .btn-danger{background:#fee2e2;color:#dc2626}.btn-danger:hover{background:#fecaca}
  .btn-sm{padding:5px 10px;font-size:12px}
  .form-row{display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:10px;align-items:end}
  label{display:block;font-size:12px;font-weight:500;color:#374151;margin-bottom:4px}
  .auth-wrap{max-width:360px;margin:80px auto;text-align:center}
  .auth-wrap h1{margin-bottom:8px}
  .auth-wrap p{color:#6b7280;font-size:14px;margin-bottom:24px}
  .auth-wrap input{margin-bottom:12px}
  #error{color:#dc2626;font-size:13px;margin-top:8px;display:none}
  .empty{color:#9ca3af;font-size:14px;padding:16px 10px}
  .editing td{background:#eff6ff}
  .btn-copy{background:#f3f4f6;color:#374151;border:1px solid #d1d5db}.btn-copy:hover{background:#e5e7eb}
  .btn-copy.copied{background:#d1fae5;color:#065f46;border-color:#6ee7b7}
  .script-row td{background:#f9fafb;padding:8px 10px}
  .script-box{display:flex;align-items:center;gap:8px;background:#fff;border:1px solid #e5e7eb;border-radius:6px;padding:8px 12px}
  .script-box code{flex:1;font-size:11px;color:#374151;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:monospace}
</style>
</head>
<body>

<div id="auth-screen" class="auth-wrap">
  <h1>Admin Sign In</h1>
  <p>Team members: enter your username and password.<br>Admin key holders: leave username blank.</p>
  <label for="username-input" style="text-align:left;display:block;font-size:12px;font-weight:500;color:#374151;margin-bottom:4px">Username</label>
  <input type="text" id="username-input" placeholder="Leave blank if using admin key" autocomplete="username" style="margin-bottom:12px">
  <label for="key-input" style="text-align:left;display:block;font-size:12px;font-weight:500;color:#374151;margin-bottom:4px">Password / Admin key</label>
  <input type="password" id="key-input" placeholder="Your password or admin key" autocomplete="current-password">
  <button class="btn btn-primary" style="width:100%;margin-top:16px" onclick="login()">Sign in</button>
  <div id="error">Invalid username or password</div>
  <a href="/admin/forgot-password" style="display:block;margin-top:14px;font-size:13px;color:#6b7280;text-decoration:none">Forgot your password?</a>
</div>

<div id="app" class="wrap" style="display:none">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
    <h1>Feedback Widget</h1>
    <span id="logged-in-user" style="font-size:13px;color:#6b7280"></span>
  </div>
  <p class="sub">Manage registered sites and their contact emails.</p>

  <div class="card">
    <h2>Add Site</h2>
    <div class="form-row">
      <div><label>Site ID</label><input id="new-id" placeholder="becomedistinct"></div>
      <div><label>Client Name</label><input id="new-name" placeholder="BecomDistinct"></div>
      <div><label>Client Email</label><input id="new-email" type="email" placeholder="client@example.com"></div>
      <div style="padding-bottom:1px"><button class="btn btn-primary" onclick="addSite()">Add</button></div>
    </div>
    <div id="add-error" style="color:#dc2626;font-size:13px;margin-top:8px;display:none"></div>
  </div>

  <div class="card">
    <h2>Registered Sites</h2>
    <table>
      <thead><tr><th>Site ID</th><th>Client Name</th><th>Client Email</th><th></th></tr></thead>
      <tbody id="sites-tbody"><tr><td class="empty" colspan="4">Loading…</td></tr></tbody>
    </table>
    <p style="font-size:12px;color:#9ca3af;margin-top:12px">Click "Script" on any row to copy the embed tag for that site.</p>
  </div>

  <div class="card">
    <h2>Team Members</h2>
    <table>
      <thead><tr><th>Username</th><th></th></tr></thead>
      <tbody id="users-tbody"><tr><td class="empty" colspan="2">Loading…</td></tr></tbody>
    </table>

    <div style="margin-top:20px;border-top:1px solid #f3f4f6;padding-top:20px">
      <h2 style="margin-bottom:4px">Pending Invites</h2>
      <p style="font-size:12px;color:#9ca3af;margin-bottom:12px">Invites expire after 7 days.</p>
      <table>
        <thead><tr><th>Username</th><th>Email</th><th>Expires</th><th></th></tr></thead>
        <tbody id="invites-tbody"><tr><td class="empty" colspan="4">No pending invites.</td></tr></tbody>
      </table>
    </div>

    <div style="margin-top:20px;border-top:1px solid #f3f4f6;padding-top:20px">
      <h2 style="margin-bottom:12px">Invite Team Member</h2>
      <div style="display:grid;grid-template-columns:1fr 1fr auto;gap:10px;align-items:end">
        <div><label>Username</label><input id="new-uname" placeholder="alice"></div>
        <div><label>Email</label><input id="new-uemail" type="email" placeholder="alice@example.com"></div>
        <div style="padding-bottom:1px"><button class="btn btn-primary" onclick="inviteUser()">Send Invite</button></div>
      </div>
      <div id="user-error" style="color:#dc2626;font-size:13px;margin-top:8px;display:none"></div>
      <div id="user-ok" style="color:#059669;font-size:13px;margin-top:8px;display:none"></div>
    </div>
  </div>
</div>

<script>
let adminKey = '';

function login() {
  const username = document.getElementById('username-input').value.trim();
  const password = document.getElementById('key-input').value.trim();
  if (!password) return;
  document.getElementById('error').style.display = 'none';
  fetch('/api/admin/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password })
  })
    .then(r => {
      if (!r.ok) return r.json().then(e => Promise.reject(e.detail || 'Sign in failed'));
      return r.json();
    })
    .then(data => {
      adminKey = data.token;
      document.getElementById('auth-screen').style.display = 'none';
      document.getElementById('app').style.display = '';
      if (data.username) {
        document.getElementById('logged-in-user').textContent = 'Signed in as ' + data.username;
      }
      loadUsers();
      loadInvites();
      return fetch('/api/admin/sites', { headers: { 'X-Admin-Key': adminKey } });
    })
    .then(r => r.json())
    .then(sites => renderSites(sites))
    .catch(msg => {
      const el = document.getElementById('error');
      el.textContent = typeof msg === 'string' ? msg : 'Sign in failed';
      el.style.display = 'block';
    });
}

['username-input', 'key-input'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', e => {
    if (e.key === 'Enter') login();
  });
});

const API_BASE = window.location.origin;

function embedScript(id) {
  return `<script src="${API_BASE}/feedback-widget.js" data-site="${id}"><\/script>`;
}

function copyScript(id, btn) {
  navigator.clipboard.writeText(embedScript(id)).then(() => {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Script'; btn.classList.remove('copied'); }, 2000);
  });
}

function renderSites(sites) {
  const tbody = document.getElementById('sites-tbody');
  const entries = Object.entries(sites);
  if (!entries.length) {
    tbody.innerHTML = '<tr><td class="empty" colspan="4">No sites yet — add one above.</td></tr>';
    return;
  }
  tbody.innerHTML = entries.map(([id, s]) => `
    <tr id="row-${id}">
      <td><code style="font-size:13px">${id}</code></td>
      <td id="name-${id}">${s.client_name}</td>
      <td id="email-${id}">${s.client_email}</td>
      <td><div class="actions">
        <button class="btn btn-sm btn-copy" id="copy-${id}" onclick="copyScript('${id}', this)">Script</button>
        <button class="btn btn-sm btn-primary" onclick="startEdit('${id}','${s.client_name}','${s.client_email}')">Edit</button>
        <button class="btn btn-sm btn-danger" onclick="deleteSite('${id}')">Delete</button>
      </div></td>
    </tr>`).join('');
}

function startEdit(id, name, email) {
  const row = document.getElementById('row-' + id);
  row.classList.add('editing');
  document.getElementById('name-' + id).innerHTML = `<input id="edit-name-${id}" value="${name}" style="min-width:140px">`;
  document.getElementById('email-' + id).innerHTML = `<input id="edit-email-${id}" type="email" value="${email}" style="min-width:180px">`;
  row.querySelector('.actions').innerHTML = `
    <button class="btn btn-sm btn-primary" onclick="saveEdit('${id}')">Save</button>
    <button class="btn btn-sm" style="background:#f3f4f6" onclick="loadSites()">Cancel</button>`;
}

function saveEdit(id) {
  const name  = document.getElementById('edit-name-' + id).value.trim();
  const email = document.getElementById('edit-email-' + id).value.trim();
  if (!name || !email) return;
  const form = new FormData();
  form.append('client_name', name);
  form.append('client_email', email);
  fetch('/api/admin/sites/' + id, { method: 'PUT', headers: { 'X-Admin-Key': adminKey }, body: form })
    .then(r => r.json()).then(() => loadSites());
}

function deleteSite(id) {
  if (!confirm('Delete site "' + id + '"? This cannot be undone.')) return;
  fetch('/api/admin/sites/' + id, { method: 'DELETE', headers: { 'X-Admin-Key': adminKey } })
    .then(() => loadSites());
}

function addSite() {
  const id    = document.getElementById('new-id').value.trim();
  const name  = document.getElementById('new-name').value.trim();
  const email = document.getElementById('new-email').value.trim();
  const errEl = document.getElementById('add-error');
  errEl.style.display = 'none';
  if (!id || !name || !email) { errEl.textContent = 'All fields are required.'; errEl.style.display = 'block'; return; }
  const form = new FormData();
  form.append('site_id', id); form.append('client_name', name); form.append('client_email', email);
  fetch('/api/admin/sites', { method: 'POST', headers: { 'X-Admin-Key': adminKey }, body: form })
    .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e.detail)))
    .then(() => {
      document.getElementById('new-id').value = '';
      document.getElementById('new-name').value = '';
      document.getElementById('new-email').value = '';
      loadSites();
    })
    .catch(msg => { errEl.textContent = msg || 'Error adding site.'; errEl.style.display = 'block'; });
}

function loadSites() {
  fetch('/api/admin/sites', { headers: { 'X-Admin-Key': adminKey } })
    .then(r => r.json()).then(renderSites);
}

function loadUsers() {
  fetch('/api/admin/users', { headers: { 'X-Admin-Key': adminKey } })
    .then(r => r.json()).then(data => renderUsers(data.users || []));
}

function renderUsers(users) {
  const tbody = document.getElementById('users-tbody');
  if (!users.length) {
    tbody.innerHTML = '<tr><td class="empty" colspan="2">No team members yet.</td></tr>';
    return;
  }
  tbody.innerHTML = users.map(u => `
    <tr>
      <td>${u}</td>
      <td style="text-align:right"><div class="actions" style="justify-content:flex-end">
        <button class="btn btn-sm btn-secondary" onclick="resetUser('${u}', this)">Reset Password</button>
        <button class="btn btn-sm btn-danger" onclick="deleteUser('${u}')">Remove</button>
      </div></td>
    </tr>`).join('');
}

function resetUser(username, btn) {
  if (!confirm('Send a password reset email to "' + username + '"?')) return;
  btn.disabled = true; btn.textContent = 'Sending…';
  fetch('/api/admin/users/' + username + '/reset', { method: 'POST', headers: { 'X-Admin-Key': adminKey } })
    .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e.detail)))
    .then(() => { btn.textContent = 'Sent!'; setTimeout(() => { btn.disabled = false; btn.textContent = 'Reset Password'; }, 3000); })
    .catch(msg => { btn.disabled = false; btn.textContent = 'Reset Password'; alert(msg || 'Failed to send reset email.'); });
}

function deleteUser(username) {
  if (!confirm('Remove "' + username + '"? They will no longer be able to sign in.')) return;
  fetch('/api/admin/users/' + username, { method: 'DELETE', headers: { 'X-Admin-Key': adminKey } })
    .then(() => loadUsers());
}

function loadInvites() {
  fetch('/api/admin/invites', { headers: { 'X-Admin-Key': adminKey } })
    .then(r => r.json()).then(data => renderInvites(data.invites || []));
}

function renderInvites(invites) {
  const tbody = document.getElementById('invites-tbody');
  if (!invites.length) {
    tbody.innerHTML = '<tr><td class="empty" colspan="4">No pending invites.</td></tr>';
    return;
  }
  tbody.innerHTML = invites.map(i => `
    <tr>
      <td>${i.username}</td>
      <td>${i.email}</td>
      <td style="color:#6b7280;font-size:12px">${i.expires_in_hours}h</td>
      <td style="text-align:right"><button class="btn btn-sm btn-danger" onclick="revokeInvite('${i.username}')">Revoke</button></td>
    </tr>`).join('');
}

function revokeInvite(username) {
  if (!confirm('Revoke invite for "' + username + '"?')) return;
  fetch('/api/admin/invites/' + username, { method: 'DELETE', headers: { 'X-Admin-Key': adminKey } })
    .then(() => loadInvites());
}

function inviteUser() {
  const username = document.getElementById('new-uname').value.trim();
  const email = document.getElementById('new-uemail').value.trim();
  const errEl = document.getElementById('user-error');
  const okEl = document.getElementById('user-ok');
  errEl.style.display = 'none'; okEl.style.display = 'none';
  if (!username || !email) { errEl.textContent = 'Both fields are required.'; errEl.style.display = 'block'; return; }
  fetch('/api/admin/invite', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Admin-Key': adminKey },
    body: JSON.stringify({ username, email })
  })
    .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e.detail)))
    .then(() => {
      document.getElementById('new-uname').value = '';
      document.getElementById('new-uemail').value = '';
      okEl.textContent = 'Invite sent to ' + email;
      okEl.style.display = 'block';
      loadInvites();
    })
    .catch(msg => { errEl.textContent = msg || 'Error sending invite.'; errEl.style.display = 'block'; });
}
</script>
</body>
</html>""")


@app.get("/api/feedback/recordings", dependencies=[Depends(require_admin)])
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
    device_ctx: dict | None = None,
) -> str:
    """Build a clean HTML ticket description for Zoho Desk."""
    playback_url = f"{BASE_URL}/api/feedback/recordings/{submission_id}/view"
    submitted_at = time.strftime("%B %d, %Y at %H:%M UTC", time.gmtime())
    ctx = device_ctx or {}

    transcript_html = (
        f"<p>{transcript}</p>"
        if transcript
        else "<p><em>No speech detected in recording.</em></p>"
    )

    recording_section = (
        f'<p><a href="{playback_url}">▶ Watch Recording</a></p>'
        if mode == "desktop"
        else f'<p><a href="{playback_url}">▶ Listen to Audio</a></p>'
    )

    size_str = (
        f"{total_size / 1024 / 1024:.1f} MB"
        if total_size > 1024 * 1024
        else f"{total_size / 1024:.0f} KB"
    )

    # Device / session rows (only include non-empty values)
    def row(label: str, value: str) -> str:
        return f'  <tr><td><strong>{label}</strong></td><td>{value}</td></tr>\n' if value else ""

    scroll = ctx.get("scroll_pos", "")
    scroll_fmt = f"{scroll} px" if scroll else ""
    screen = ctx.get("screen_size", "")
    dpr = ctx.get("pixel_ratio", "")
    screen_fmt = f"{screen}  ({dpr}× DPR)" if screen and dpr else screen
    device = ctx.get("device_model", "")
    os_plat = ctx.get("os_platform", "")
    os_ver = ctx.get("os_version", "")
    device_fmt = " · ".join(filter(None, [device, os_plat, os_ver]))

    device_rows = (
        row("Page title",  ctx.get("page_title", ""))
        + row("Scroll pos",  scroll_fmt)
        + row("Screen",      screen_fmt)
        + row("Viewport",    ctx.get("viewport_size", ""))
        + row("Device",      device_fmt)
        + row("Language",    ctx.get("language", ""))
        + row("Timezone",    ctx.get("timezone", ""))
        + row("Network",     ctx.get("network_type", ""))
    )

    return f"""<h2>What they said</h2>
{transcript_html}

<h2>Recording</h2>
{recording_section}

<h2>Details</h2>
<table>
{row("Page", f'<a href="{page_url}">{page_url}</a>')}
  <tr><td><strong>Client</strong></td><td>{client_name}</td></tr>
  <tr><td><strong>Submitted</strong></td><td>{submitted_at}</td></tr>
  <tr><td><strong>File size</strong></td><td>{size_str}</td></tr>
{device_rows}  <tr><td><strong>User agent</strong></td><td>{user_agent}</td></tr>
  <tr><td><strong>Submission ID</strong></td><td>{submission_id}</td></tr>
</table>"""


NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "vince@becomedistinct.com")
NOTIFY_FROM  = os.getenv("NOTIFY_FROM",  "Feedback Widget <assist@becomedistinct.com>")


def _resend_send(subject: str, html: str, to: str) -> None:
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    if not api_key:
        logger.error("RESEND_API_KEY not set — cannot send email")
        return
    resend.api_key = api_key
    try:
        resend.Emails.send({
            "from": NOTIFY_FROM,
            "to": [to],
            "subject": subject,
            "html": html,
        })
        logger.info("Email sent to %s: %s", to, subject)
    except Exception as e:
        logger.error("Resend email failed: %s", e)


async def _send_email(subject: str, html: str, to: str | None = None) -> None:
    await asyncio.to_thread(_resend_send, subject, html, to or NOTIFY_EMAIL)


async def send_client_success_email(submission_id: str, meta: dict) -> None:
    """Tell the client their feedback was received and a ticket created."""
    client_name  = meta.get("client_name", meta.get("site_id", "unknown"))
    client_email = meta.get("submitter_email") or meta.get("client_email", "")
    if not client_email:
        return
    page_url     = meta.get("page_url", "")
    transcript   = (meta.get("transcript") or "").strip()
    mode         = meta.get("mode", "desktop")
    playback_url = f"{BASE_URL}/api/feedback/recordings/{submission_id}/view"
    submitted_at = time.strftime("%B %d, %Y at %H:%M UTC", time.gmtime())

    transcript_html = (
        f"<p style='color:#374151;line-height:1.6'>{transcript[:600]}{'…' if len(transcript) > 600 else ''}</p>"
        if transcript else "<p style='color:#9ca3af;font-style:italic'>No speech detected.</p>"
    )
    media_label = "Watch Recording" if mode == "desktop" else "Listen to Audio"

    html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;padding:24px;color:#111827">
  <h2 style="margin:0 0 4px;font-size:20px">Your feedback was received</h2>
  <p style="margin:0 0 24px;color:#6b7280;font-size:14px">{submitted_at}</p>
  <p style="margin:0 0 20px;line-height:1.6">Hi {client_name},<br><br>
  Thanks for submitting feedback. A support ticket has been created and our team will review it shortly.</p>

  <h3 style="font-size:13px;font-weight:600;color:#374151;margin:0 0 8px;text-transform:uppercase;letter-spacing:.05em">What you said</h3>
  {transcript_html}

  <table style="width:100%;border-collapse:collapse;font-size:14px;margin:20px 0">
    <tr><td style="padding:4px 12px 4px 0;color:#6b7280;white-space:nowrap">Page</td>
        <td style="padding:4px 0"><a href="{page_url}" style="color:#2563eb">{page_url}</a></td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#6b7280;white-space:nowrap">Submission</td>
        <td style="padding:4px 0;font-family:monospace;font-size:12px">{submission_id}</td></tr>
  </table>

  <a href="{playback_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:500">▶ {media_label}</a>
</div>"""

    await _send_email(f"Feedback received — {client_name}", html, to=client_email)


async def send_client_failure_email(submission_id: str, meta: dict) -> None:
    """Tell the client their recording was saved but the ticket had an issue."""
    client_name  = meta.get("client_name", meta.get("site_id", "unknown"))
    client_email = meta.get("submitter_email") or meta.get("client_email", "")
    if not client_email:
        return
    submitted_at = time.strftime("%B %d, %Y at %H:%M UTC", time.gmtime())

    html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;padding:24px;color:#111827">
  <h2 style="margin:0 0 4px;font-size:20px">We received your feedback</h2>
  <p style="margin:0 0 24px;color:#6b7280;font-size:14px">{submitted_at}</p>
  <p style="line-height:1.6">Hi {client_name},<br><br>
  Your feedback recording was saved successfully. We ran into a minor technical issue creating your support ticket,
  but our team has been notified and will follow up with you directly.</p>
  <p style="margin-top:16px;color:#6b7280;font-size:13px">Reference: <code>{submission_id}</code></p>
</div>"""

    await _send_email(f"Feedback received — {client_name}", html, to=client_email)


async def send_desk_failure_alert(submission_id: str, error: str, meta: dict) -> None:
    """Alert the desk when Zoho ticket creation fails — technical details + retry."""
    client_name  = meta.get("client_name", meta.get("site_id", "unknown"))
    page_url     = meta.get("page_url", "")
    playback_url = f"{BASE_URL}/api/feedback/recordings/{submission_id}/view"
    retry_url    = f"{BASE_URL}/api/admin/retry/{submission_id}"

    html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;padding:24px;color:#111827">
  <h2 style="margin:0 0 4px;font-size:20px;color:#dc2626">⚠️ Zoho Ticket Failed</h2>
  <p style="margin:0 0 24px;color:#6b7280;font-size:14px">A submission was saved but the Zoho Desk ticket could not be created.</p>
  <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:24px">
    <tr><td style="padding:4px 12px 4px 0;color:#6b7280;white-space:nowrap">Client</td><td style="padding:4px 0">{client_name}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#6b7280;white-space:nowrap">Page</td><td style="padding:4px 0"><a href="{page_url}" style="color:#2563eb">{page_url}</a></td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#6b7280;white-space:nowrap">Error</td><td style="padding:4px 0;color:#dc2626;font-family:monospace;font-size:12px">{error[:300]}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#6b7280;white-space:nowrap">Submission ID</td><td style="padding:4px 0;font-family:monospace;font-size:12px">{submission_id}</td></tr>
  </table>
  <a href="{playback_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:500;margin-right:12px">View Recording</a>
  <a href="{retry_url}" style="display:inline-block;background:#dc2626;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:500">Retry Zoho</a>
</div>"""

    await _send_email(f"⚠️ Zoho Failed — {client_name}", html)


# Keep old name as alias so existing calls still work during transition
async def send_alert_email(submission_id: str, error: str, meta: dict) -> None:
    await send_desk_failure_alert(submission_id, error, meta)


def mark_zoho_failed(meta_path: Path, meta: dict, error: str) -> None:
    """Stamp the metadata file so failed submissions are easy to find."""
    meta["zoho_failed"] = True
    meta["zoho_error_detail"] = error
    meta["zoho_failed_at"] = time.time()
    meta_path.write_text(json.dumps(meta, indent=2))


@app.post("/api/admin/test-failure-emails", dependencies=[Depends(require_admin)])
async def test_failure_emails():
    """Fire both failure email paths with fake data to verify Resend + routing."""
    fake_id = "test-" + str(uuid.uuid4())[:8]
    fake_meta = {
        "id": fake_id,
        "site_id": "becomedistinct",
        "client_name": "BecomDistinct (TEST)",
        "client_email": NOTIFY_EMAIL,
        "page_url": "https://becomedistinct.com/test",
        "transcript": "This is a simulated failure test.",
        "mode": "desktop",
    }
    fake_error = "Simulated Zoho API error: 403 Forbidden"
    await asyncio.gather(
        send_desk_failure_alert(fake_id, fake_error, fake_meta),
        send_client_failure_email(fake_id, fake_meta),
    )
    return {"status": "sent", "submission_id": fake_id, "to": NOTIFY_EMAIL}


@app.get("/api/admin/failed-submissions", dependencies=[Depends(require_admin)])
async def list_failed_submissions():
    """Return all submissions where Zoho ticket creation failed."""
    failed = []
    for site_dir in sorted(SUBMISSIONS_DIR.iterdir()):
        if not site_dir.is_dir():
            continue
        for meta_file in sorted(site_dir.glob("*.json"), reverse=True):
            try:
                meta = json.loads(meta_file.read_text())
                if meta.get("zoho_failed"):
                    meta["playback_url"] = f"{BASE_URL}/api/feedback/recordings/{meta['id']}/view"
                    failed.append(meta)
            except Exception:
                continue
    return {"count": len(failed), "submissions": failed}


@app.post("/api/admin/retry/{submission_id}", dependencies=[Depends(require_admin)])
async def retry_zoho(submission_id: str):
    """Re-attempt Zoho ticket creation for a failed submission."""
    result = _find_submission(submission_id)
    if not result:
        raise HTTPException(404, "Submission not found")
    site_dir, meta = result
    meta_path = site_dir / f"{submission_id}.json"

    if not meta.get("zoho_failed") and meta.get("ticket_id"):
        return {"status": "already_posted", "ticket_id": meta["ticket_id"]}

    mode = meta.get("mode", "desktop")
    transcript = meta.get("transcript")
    device_ctx = {
        k: meta.get(k, "")
        for k in ("page_title", "scroll_pos", "screen_size", "viewport_size",
                  "pixel_ratio", "language", "timezone", "network_type",
                  "device_model", "os_platform", "os_version")
    }

    description = build_ticket_description(
        client_name=meta.get("client_name", meta.get("site_id", "")),
        site_id=meta["site_id"],
        page_url=meta.get("page_url", ""),
        user_agent=meta.get("user_agent", ""),
        total_size=meta.get("file_size_bytes", 0),
        submission_id=submission_id,
        transcript=transcript,
        mode=mode,
        device_ctx=device_ctx,
    )

    subject_prefix = "Mobile Feedback" if mode == "mobile" else "Screen Feedback"
    ticket_id = None
    error = None
    try:
        ticket_id = await create_ticket(
            subject=f"{subject_prefix} — {meta.get('client_name', meta['site_id'])}",
            description=description,
            contact_email=meta["client_email"],
        )
        logger.info("Retry: created Zoho ticket %s for submission %s", ticket_id, submission_id)

        # Try to attach media
        attach_candidates = [
            site_dir / f"{submission_id}.mp4",
            site_dir / f"{submission_id}.webm",
        ]
        for path in attach_candidates:
            if path.exists():
                try:
                    await attach_file(ticket_id, path)
                except Exception as e:
                    logger.warning("Retry attachment failed: %s", e)
                break

        # Clear failed flag
        meta["zoho_failed"] = False
        meta["ticket_id"] = ticket_id
        meta_path.write_text(json.dumps(meta, indent=2))
    except Exception as e:
        error = str(e)
        logger.error("Retry failed for %s: %s", submission_id, e)

    return {"submission_id": submission_id, "ticket_id": ticket_id, "error": error}


@app.post("/api/feedback/submit")
async def submit_feedback(
    video: UploadFile = File(...),
    site_id: str = Form(...),
    page_url: str = Form(""),
    user_agent: str = Form(""),
    submitter_email: str = Form(""),
    page_title: str = Form(""),
    scroll_pos: str = Form(""),
    screen_size: str = Form(""),
    viewport_size: str = Form(""),
    pixel_ratio: str = Form(""),
    language: str = Form(""),
    timezone: str = Form(""),
    network_type: str = Form(""),
    device_model: str = Form(""),
    os_platform: str = Form(""),
    os_version: str = Form(""),
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

    logger.info("Saved recording: %s site=%s (%.1f MB)", submission_id, site_id, total_size / 1024 / 1024)

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

    device_ctx = {
        "page_title": page_title, "scroll_pos": scroll_pos,
        "screen_size": screen_size, "viewport_size": viewport_size,
        "pixel_ratio": pixel_ratio, "language": language,
        "timezone": timezone, "network_type": network_type,
        "device_model": device_model, "os_platform": os_platform,
        "os_version": os_version,
    }

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
        "submitter_email": submitter_email,
        **device_ctx,
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
        device_ctx=device_ctx,
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
        meta["ticket_id"] = ticket_id
        meta_path.write_text(json.dumps(meta, indent=2))
        logger.info("Created Zoho ticket: %s", ticket_id)
        await send_client_success_email(submission_id, meta)
    except Exception as e:
        logger.error("Zoho ticket creation failed: %s", e)
        zoho_error = f"Ticket creation: {e}"
        mark_zoho_failed(meta_path, meta, zoho_error)
        await asyncio.gather(
            send_desk_failure_alert(submission_id, zoho_error, meta),
            send_client_failure_email(submission_id, meta),
        )

    # Attach video file separately (so ticket exists even if attachment fails)
    if ticket_id:
        try:
            await attach_file(ticket_id, attach_path)
            logger.info("Attached %s to ticket %s", attach_path.suffix, ticket_id)
        except Exception as e:
            logger.error("Zoho attachment failed: %s", e)
            zoho_error = f"Attachment: {e}"
            mark_zoho_failed(meta_path, meta, zoho_error)
            await send_desk_failure_alert(submission_id, zoho_error, meta)

    return JSONResponse({
        "submission_id": submission_id,
        "ticket_id": ticket_id,
        "transcript": transcript[:200] if transcript else None,
        "zoho_error": zoho_error,
    })


@app.post("/api/feedback/submit-mobile")
async def submit_mobile_feedback(
    audio: UploadFile = File(...),
    site_id: str = Form(...),
    page_url: str = Form(""),
    user_agent: str = Form(""),
    submitter_email: str = Form(""),
    page_title: str = Form(""),
    scroll_pos: str = Form(""),
    screen_size: str = Form(""),
    viewport_size: str = Form(""),
    pixel_ratio: str = Form(""),
    language: str = Form(""),
    timezone: str = Form(""),
    network_type: str = Form(""),
    device_model: str = Form(""),
    os_platform: str = Form(""),
    os_version: str = Form(""),
):
    """Mobile feedback: audio recording + device/session context."""
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

    logger.info("Saved mobile audio: %s site=%s (%.1f KB)", submission_id, site_id, total_size / 1024)

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

    device_ctx = {
        "page_title": page_title, "scroll_pos": scroll_pos,
        "screen_size": screen_size, "viewport_size": viewport_size,
        "pixel_ratio": pixel_ratio, "language": language,
        "timezone": timezone, "network_type": network_type,
        "device_model": device_model, "os_platform": os_platform,
        "os_version": os_version,
    }

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
        "submitter_email": submitter_email,
        **device_ctx,
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
        device_ctx=device_ctx,
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
        meta["ticket_id"] = ticket_id
        meta_path.write_text(json.dumps(meta, indent=2))
        logger.info("Created Zoho ticket: %s", ticket_id)
        await send_client_success_email(submission_id, meta)
    except Exception as e:
        logger.error("Zoho ticket creation failed: %s", e)
        zoho_error = f"Ticket creation: {e}"
        mark_zoho_failed(meta_path, meta, zoho_error)
        await asyncio.gather(
            send_desk_failure_alert(submission_id, zoho_error, meta),
            send_client_failure_email(submission_id, meta),
        )

    # Attach audio to ticket
    if ticket_id:
        try:
            await attach_file(ticket_id, audio_path)
            logger.info("Attached audio to ticket %s", ticket_id)
        except Exception as e:
            logger.error("Audio attachment failed: %s", e)
            zoho_error = f"Attachment: {e}"
            mark_zoho_failed(meta_path, meta, zoho_error)
            await send_desk_failure_alert(submission_id, zoho_error, meta)

    return JSONResponse({
        "submission_id": submission_id,
        "ticket_id": ticket_id,
        "transcript": transcript[:200] if transcript else None,
        "zoho_error": zoho_error,
    })

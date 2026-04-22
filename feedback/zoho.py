"""
Zoho Desk API client — OAuth token management + ticket creation + file attachment.
"""
import time
import httpx
import os
from pathlib import Path

# Token cache
_token_cache: dict = {"access_token": None, "expires_at": 0}

ZOHO_ACCOUNTS_URL = os.getenv("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com")
ZOHO_DESK_URL = os.getenv("ZOHO_DESK_URL", "https://desk.zoho.com/api/v1")


def _env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing environment variable: {key}")
    return val


async def get_access_token() -> str:
    """Get a valid access token, refreshing if expired."""
    now = time.time()
    if _token_cache["access_token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["access_token"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ZOHO_ACCOUNTS_URL}/oauth/v2/token",
            params={
                "refresh_token": _env("ZOHO_REFRESH_TOKEN"),
                "client_id": _env("ZOHO_CLIENT_ID"),
                "client_secret": _env("ZOHO_CLIENT_SECRET"),
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3600)
    return _token_cache["access_token"]


async def create_ticket(
    subject: str,
    description: str,
    contact_email: str,
) -> str:
    """Create a Zoho Desk ticket. Returns the ticket ID."""
    token = await get_access_token()
    department_id = _env("ZOHO_DEPARTMENT_ID")

    payload: dict = {
        "subject": subject,
        "description": description,
        "descriptionType": "html",
        "departmentId": department_id,
        "channel": "Web",
        "contact": {
            "email": contact_email,
        },
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ZOHO_DESK_URL}/tickets",
            json=payload,
            headers={
                "Authorization": f"Zoho-oauthtoken {token}",
                "orgId": _env("ZOHO_ORG_ID"),
                "Content-Type": "application/json",
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"{resp.status_code}: {resp.text}")
        return resp.json()["id"]


async def attach_file(ticket_id: str, file_path: Path) -> dict:
    """Attach a file to an existing Zoho Desk ticket."""
    token = await get_access_token()

    async with httpx.AsyncClient(timeout=300) as client:
        with open(file_path, "rb") as f:
            resp = await client.post(
                f"{ZOHO_DESK_URL}/tickets/{ticket_id}/attachments",
                files={"file": (file_path.name, f, {
                    ".mp4": "video/mp4",
                    ".webm": "video/webm",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                }.get(file_path.suffix, "application/octet-stream"))},
                headers={
                    "Authorization": f"Zoho-oauthtoken {token}",
                    "orgId": _env("ZOHO_ORG_ID"),
                },
            )
        resp.raise_for_status()
        return resp.json()

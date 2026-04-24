# Feedback Widget

Embeddable screen-recording feedback widget + FastAPI backend.

## Structure
- `feedback_ui/` — TypeScript/Vite widget (Shadow DOM, IIFE bundle)
- `feedback/` — FastAPI backend (Python)
- `Dockerfile` — Multi-stage build (Node → Python + ffmpeg + faster-whisper)
- `railway.toml` — Railway deployment config

## Widget
Injected via Google Tag Manager. Records screen+mic on desktop (`getDisplayMedia`),
or audio-only on mobile (`getUserMedia`). Both paths collect rich device/session
metadata (scroll position, screen size, timezone, device model, etc.).

## Backend
- `POST /api/feedback/submit` — receive desktop video, transcribe, create Zoho Desk ticket
- `POST /api/feedback/submit-mobile` — receive mobile audio, transcribe, create Zoho Desk ticket
- `GET /api/feedback/recordings/{id}/view` — browser viewer (transcript + video/audio)
- `GET /feedback-widget.js` — serve built widget bundle
- `GET /admin` — site management UI (requires ADMIN_KEY)
- Admin endpoints require `X-Admin-Key` header (not query param — key has special chars)

## Deploy
```
cd "D:\feedback-widget"
railway up --service 899b46bd-a8c2-4746-85d0-c7796ffed920 --environment 988b0255-348d-481f-9a28-cab40e5f9f41
```
For env-var-only changes (no code change), use restart instead of up:
```
railway restart --service 899b46bd-a8c2-4746-85d0-c7796ffed920 --yes
```

## Env vars (Railway)
- `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`
- `ZOHO_ORG_ID`, `ZOHO_DEPARTMENT_ID`
- `ZOHO_ACCOUNTS_URL`, `ZOHO_DESK_URL`
- `BASE_URL` — public URL of this service
- `RESEND_API_KEY` — Resend API key (no trailing spaces)
- `NOTIFY_EMAIL` — desk alert recipient (default: vince@becomedistinct.com)
- `NOTIFY_FROM` — sender address (default: Feedback Widget <assist@becomedistinct.com>)
- `ADMIN_KEY` — protects /admin UI and admin API endpoints
- `CORS_ORIGINS` — optional comma-separated list to override default allowed origins

## Live URLs
- API: https://feedback-api-production-7e6f.up.railway.app
- GitHub: https://github.com/Becomedistinct/feedback-widget

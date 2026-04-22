# Feedback Widget

Embeddable screen-recording feedback widget + FastAPI backend.

## Structure
- `feedback_ui/` — TypeScript/Vite widget (Shadow DOM, IIFE bundle)
- `feedback/` — FastAPI backend (Python)
- `Dockerfile` — Multi-stage build (Node → Python + ffmpeg + faster-whisper)
- `railway.toml` — Railway deployment config

## Widget
Injected via Google Tag Manager. Records screen+mic on desktop (`getDisplayMedia`), 
or audio+screenshots on mobile (`getUserMedia` + html2canvas).

## Backend
- `POST /api/feedback/submit` — receive recording, transcribe, create Zoho Desk ticket
- `GET /api/feedback/recordings/{id}/view` — browser viewer (transcript + video/audio/screenshots)
- `GET /feedback-widget.js` — serve built widget bundle

## Deploy
```
cd "D:\Claude Code\feedback_widget"
railway up
```

## Env vars (Railway)
- `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`
- `ZOHO_ORG_ID`, `ZOHO_DEPARTMENT_ID`
- `ZOHO_ACCOUNTS_URL`, `ZOHO_DESK_URL`
- `BASE_URL` — public URL of this service

## Live URLs
- API: https://feedback-api-production-7e6f.up.railway.app
- GitHub: https://github.com/Becomedistinct/feedback-widget

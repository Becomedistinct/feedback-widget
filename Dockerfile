# Feedback Widget Service
# Stage 1: Build widget JS bundle, Stage 2: Python API with ffmpeg + whisper

# ── Stage 1: Build the widget JS bundle ──────────────────────────────────────
FROM node:24-slim AS widget-builder

WORKDIR /build
COPY feedback_ui/package*.json feedback_ui/package-lock.json* ./
RUN npm install
COPY feedback_ui/ ./
RUN npm run build


# ── Stage 2: Python runtime ──────────────────────────────────────────────────
FROM python:3.11-slim

# Install ffmpeg for audio extraction from video recordings
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY feedback/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY feedback/main.py feedback/zoho.py feedback/transcribe.py feedback/sites.json ./feedback/

# Copy built widget JS into static directory
COPY --from=widget-builder /build/dist/ ./static/

# Create submissions directory
RUN mkdir -p /app/feedback/submissions

# Pre-download the whisper model during build so first request isn't slow
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')" || true

EXPOSE 8080

CMD ["sh", "-c", "python -m uvicorn feedback.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

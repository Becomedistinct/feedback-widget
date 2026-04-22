# ── Stage 1: Build the widget JS bundle ──────────────────────────────────────
FROM node:24-slim AS widget-builder

WORKDIR /build
COPY feedback_ui/package*.json ./
RUN npm install
COPY feedback_ui/ ./
RUN npm run build


# ── Stage 2: Python runtime ──────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
COPY feedback/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY feedback/main.py feedback/zoho.py feedback/sites.json ./feedback/

# Copy built widget JS into static directory
COPY --from=widget-builder /build/dist/ ./static/

# Create submissions directory
RUN mkdir -p /app/feedback/submissions

EXPOSE 8080

CMD ["sh", "-c", "python -m uvicorn feedback.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

# ══════════════════════════════════════════════════════════════
# Stage 1: Build Vite React Frontend
# ══════════════════════════════════════════════════════════════
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# ══════════════════════════════════════════════════════════════
# Stage 2: Unified Python Server (Flask + Gunicorn + SQLite)
# ══════════════════════════════════════════════════════════════
FROM python:3.12-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=5001 \
    FLASK_ENV=production

# Install system dependencies (curl + gzip)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gzip \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY ["Backend(Task)/requirements.txt", "./requirements.txt"]
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy backend files
COPY ["Backend(Task)/", "./Backend(Task)/"]

# Copy built frontend assets to static dist directory
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

WORKDIR "/app/Backend(Task)"

# Reassemble multi-part compressed enrichment database & build search index
RUN if [ ! -f catalog_enrichment.db ]; then \
        if ls catalog_enrichment.db.gz.part_* 1> /dev/null 2>&1; then \
            echo "Reassembling catalog_enrichment.db from multi-part archives..."; \
            cat catalog_enrichment.db.gz.part_* > catalog_enrichment.db.gz && \
            gzip -d -f -k catalog_enrichment.db.gz; \
        elif [ -f catalog_enrichment.db.gz ]; then \
            echo "Decompressing catalog_enrichment.db.gz..."; \
            gzip -d -f -k catalog_enrichment.db.gz; \
        fi; \
    fi && \
    python build_search_index.py || true

EXPOSE 5001

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:$PORT app:app --workers 2 --threads 4 --timeout 120"]

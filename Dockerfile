FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Optional accounts. Vite inlines these into the bundle at build time, and .dockerignore
# keeps frontend/.env out, so they must arrive as build args (Render passes the service's
# environment variables as build args). Empty = accounts off. Both values are public.
ARG VITE_SUPABASE_URL=""
ARG VITE_SUPABASE_PUBLISHABLE_KEY=""
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DATA_MODE=live \
    REGION=asheville \
    WARM_REPLAY_CACHE=false

# Geospatial wheels still need system XML (Expat) and OpenMP libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 haven

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=haven:haven backend/app/ ./app/
# Ship prepared terrain, roads and replay data; startup requires no preparation downloads.
COPY --chown=haven:haven backend/data/ ./data/
COPY --from=frontend /build/frontend/dist/ /app/frontend/dist/

USER haven
# Catch missing native libraries during the build, without starting live pollers.
RUN python -c "from app.main import app; assert app"
EXPOSE 8000
# One worker shares the in-memory graphs, polling jobs and SSE subscribers.
# exec forwards shutdown signals; the shell expands Render's assigned PORT.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1"]

# --- Frontend build (React/Vite) -------------------------------------------------
FROM node:24-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Django app ------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Outside /app so the dev bind mount (.:/app) doesn't hide them.
    FRONTEND_DIST_DIR=/srv/frontend \
    STATIC_ROOT=/srv/static

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
COPY --from=frontend /build/dist /srv/frontend

# Collect static files for WhiteNoise (DEBUG=false). Throwaway values: settings
# require these env vars, but nothing here touches the database or secrets.
RUN DJANGO_SECRET_KEY=collectstatic-only DATABASE_URL=sqlite:// \
    python manage.py collectstatic --noinput -v0

RUN useradd --create-home --uid 1000 app && chown -R app:app /app /srv
USER app

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

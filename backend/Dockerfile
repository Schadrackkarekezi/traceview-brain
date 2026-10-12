FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Seeding is a deliberate one-time step (docker compose exec backend python -m
# scripts.seed_db), not automatic on boot — auto-seeding would re-run against
# a real Postgres volume on every container restart. Migrations are the
# opposite: alembic upgrade head is idempotent, so running it on every boot
# is the standard "migrate then serve" pattern for a deployed container.
#
# --proxy-headers --forwarded-allow-ips="*" makes uvicorn trust the
# X-Forwarded-For header from the platform's edge proxy (Railway, Render,
# etc.) instead of seeing every request as coming from the proxy's own IP -
# without this, rate_limit.py's per-IP limiter would bucket all visitors
# together behind a single shared IP. $PORT falls back to 8000 for
# docker-compose / local runs where the platform doesn't inject one.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]

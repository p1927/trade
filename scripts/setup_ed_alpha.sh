#!/usr/bin/env bash
# Bootstrap ED-ALPHA (forked submodule) for Trade corp_events research stage.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ED_ALPHA_DIR="$ROOT/ed-alpha"

if [[ ! -d "$ED_ALPHA_DIR" ]]; then
  echo "ed-alpha submodule missing. Run: git submodule update --init ed-alpha" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  if [[ -x "/Applications/Docker.app/Contents/Resources/bin/docker" ]]; then
    export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
  fi
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required for ED-ALPHA. Install Docker Desktop and retry." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Starting Docker Desktop..."
  open -a Docker >/dev/null 2>&1 || true
  for _ in $(seq 1 90); do
    if docker info >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon not running. Start Docker Desktop manually and retry." >&2
  exit 1
fi

cd "$ED_ALPHA_DIR"

if [[ ! -f .env ]]; then
  cp .env.sample .env
  echo "Created ed-alpha/.env from sample — edit USER_EMAIL if needed."
fi

echo "==> Building ED-ALPHA backend + batch (API only; skip frontend for Trade)..."
docker compose build db backend batch

echo "==> Starting Postgres..."
docker compose up -d db

echo "==> Waiting for database health..."
for _ in $(seq 1 60); do
  if docker compose ps db 2>/dev/null | grep -q "(healthy)"; then
    break
  fi
  sleep 2
done

echo "==> Starting backend API..."
docker compose up -d backend

echo "==> Waiting for backend /health..."
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:8000/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "==> Starting batch runner profile..."
docker compose --profile batch up -d batch

echo "==> Loading SEC company tickers (required for /predictions/{ticker})..."
docker compose exec -T batch sh -lc 'cd /app && python src/fetch_company_tickers.py'

cat <<'EOF'

ED-ALPHA is up:
  API docs   http://localhost:8000/docs
  Health     http://localhost:8000/health
  Frontend   optional: docker compose up -d frontend  (UI on :3000)

Add to Trade .env:
  ED_ALPHA_BASE_URL=http://localhost:8000

Verify:
  python scripts/smoke_research_stage.py --stage corp_events --ticker AAPL

For ranked 8-K predictions (not just ticker lookup), run batch ingest inside ed-alpha:
  docker compose exec -it batch sh
  cd /app
  python src/fetch_recent_filings.py
  python src/fetch_gdelt_master_times.py
  python src/fetch_gdelt_gkg.py --start-time 202506010000 --end-time 202506072359
  python src/link_gdelt_gkg_companies.py
  python src/generate_labels.py --predict-date 20250901 --horizon-days 5 --min-days-before 120 --max-days-before 91
  # then score_gdelt_news.py with --scorer-class or OpenRouter (see ed-alpha/README.md)

EOF

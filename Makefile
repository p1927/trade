.PHONY: start status stop-searxng logs-searxng sync sync-status \
        tunnel-quick tunnel-named tunnel-stop tunnel-status tunnel-urls \
        vibe setup-vibe vibe-frontend trade

trade:
	@./trade --help

start:
	./trade start

vibe:
	./start.sh --vibe-only

setup-vibe:
	./.venv/bin/python scripts/setup_vibe.py

vibe-frontend:
	./scripts/ensure_vibe_frontend.sh

status:
	./start.sh --status --no-bootstrap

tunnel-quick:
	./trade tunnel quick

tunnel-named:
	./trade tunnel named

tunnel-stop:
	./trade tunnel stop

tunnel-status:
	./trade tunnel status

tunnel-urls:
	./trade webhooks all

stop-searxng:
	docker compose -f docker-compose.stack.yml down

logs-searxng:
	docker compose -f docker-compose.stack.yml logs -f searxng

sync:
	./scripts/sync.sh all

sync-status:
	./scripts/sync.sh status

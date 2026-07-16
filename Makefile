.PHONY: start status stop-searxng logs-searxng sync sync-status \
        tunnel-quick tunnel-restart tunnel-named tunnel-stop tunnel-status tunnel-urls \
        vibe setup-vibe vibe-frontend trade setup-ed-alpha start-ed-alpha stop-ed-alpha \
        start-daemon restart-vibe stop-vibe status-vibe

trade:
	@./trade --help

start:
	./trade start

start-daemon:
	./start.sh --daemon --no-searxng

restart-vibe:
	./scripts/restart_vibe_stack.sh

stop-vibe:
	./scripts/stop_vibe_stack.sh

status-vibe:
	./scripts/status_vibe_stack.sh

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

tunnel-restart:
	./trade tunnel restart

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

setup-ed-alpha:
	./scripts/setup_ed_alpha.sh

start-ed-alpha:
	./scripts/start_ed_alpha.sh

stop-ed-alpha:
	./scripts/stop_ed_alpha.sh

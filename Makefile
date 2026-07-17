.PHONY: start status stop-searxng stop-docker logs-searxng sync sync-status sync-ports \
        tunnel-quick tunnel-restart tunnel-named tunnel-stop tunnel-status tunnel-urls \
        vibe setup-vibe vibe-frontend trade setup-ed-alpha start-ed-alpha stop-ed-alpha \
        start-daemon restart-vibe stop-vibe status-vibe status-hub doctor

trade:
	@./trade --help

start:
	./trade start

start-daemon:
	./start.sh --daemon

restart-vibe:
	./scripts/restart_vibe_stack.sh

stop-vibe:
	./scripts/stop_vibe_stack.sh

status-vibe:
	./scripts/status_vibe_stack.sh

status-hub:
	./scripts/status_hub_stack.sh

doctor:
	./scripts/stack_doctor.sh

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
	./trade stop-docker searxng

stop-docker:
	./trade stop-docker all

logs-searxng:
	docker compose -f docker-compose.stack.yml logs -f searxng

sync:
	./scripts/sync.sh all

sync-status:
	./scripts/sync.sh status

sync-ports:
	./.venv/bin/python scripts/sync_stack_ports.py --apply

setup-ed-alpha:
	./scripts/setup_ed_alpha.sh

start-ed-alpha:
	./scripts/start_ed_alpha.sh

stop-ed-alpha:
	./scripts/stop_ed_alpha.sh

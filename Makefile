.PHONY: start status stop-searxng logs-searxng sync sync-status

start:
	./start.sh

status:
	./start.sh --status --no-bootstrap

stop-searxng:
	docker compose -f docker-compose.stack.yml down

logs-searxng:
	docker compose -f docker-compose.stack.yml logs -f searxng

sync:
	./scripts/sync.sh all

sync-status:
	./scripts/sync.sh status

.DEFAULT_GOAL := help

.PHONY: help env up down down-v logs ps check shell superuser test integration

SERVICES ?=


help:
	@echo "make up     - Build and start the project"
	@echo "make down   - Stop containers and preserve data"
	@echo "make down-v - Stop containers and delete project volumes"
	@echo "make logs   - Follow container logs"
	@echo "              Use SERVICES=backend to follow one service; Ctrl+C stops following"
	@echo "make ps     - Show service status"
	@echo "make check  - Run Django checks"
	@echo "make shell  - Open Django shell"
	@echo "make superuser - Create a Django administrator interactively"
	@echo "make test   - Run the regression suite"
	@echo "make integration - Verify a disposable real-service stack"

env:
	@test -f .env || docker run --rm \
		--user "$$(id -u):$$(id -g)" \
		-v "$(CURDIR):/work" \
		-w /work \
		python:3.12-slim-bookworm \
		python -c "from pathlib import Path; import secrets; Path('.env').write_text(Path('.env.example').read_text().replace('DJANGO_SECRET_KEY=replace-with-a-random-secret', 'DJANGO_SECRET_KEY=' + secrets.token_urlsafe(48)))"

up: env
	docker compose config --quiet
	docker compose up -d --build --wait --wait-timeout 180

down:
	docker compose down

down-v:
	docker compose down --volumes

logs:
	docker compose logs --follow --tail=100 $(SERVICES)

ps:
	docker compose ps

check:
	docker compose exec backend python manage.py check
	docker compose exec backend python manage.py makemigrations --check --dry-run
	docker compose exec nginx nginx -t

shell:
	docker compose exec backend python manage.py shell

superuser:
	docker compose exec backend python manage.py createsuperuser

test:
	docker compose exec backend python manage.py test --noinput

integration: env
	python3 scripts/integration_check.py

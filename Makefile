.DEFAULT_GOAL := help

.PHONY: help env up down logs ps check shell test integration


help:
	@echo "make up     - Build and start the project"
	@echo "make down   - Stop containers and preserve data"
	@echo "make logs   - Follow container logs"
	@echo "make ps     - Show service status"
	@echo "make check  - Run Django checks"
	@echo "make shell  - Open Django shell"
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

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

check:
	docker compose exec backend python manage.py check
	docker compose exec backend python manage.py makemigrations --check --dry-run

shell:
	docker compose exec backend python manage.py shell

test:
	docker compose exec backend python manage.py test --noinput

integration: env
	python3 scripts/integration_check.py

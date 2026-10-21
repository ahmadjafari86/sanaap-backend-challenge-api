FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app

COPY --chown=app:app . .
RUN chmod +x /app/docker/backend/entrypoint.sh \
    && mkdir -p /app/staticfiles /app/.staging \
    && chown app:app /app/staticfiles /app/.staging \
    && chmod 700 /app/.staging

USER app

EXPOSE 8000

ENTRYPOINT ["/app/docker/backend/entrypoint.sh"]
CMD ["gunicorn", "config.asgi:application", "--worker-class", "config.workers.ApplicationWorker", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", "--error-logfile", "-"]

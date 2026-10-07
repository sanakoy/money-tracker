# Зависимости ставятся отдельным слоем: он пересобирается только при правке
# pyproject.toml или uv.lock, а не при каждом изменении кода
FROM python:3.13-slim AS builder

# uv берём готовым бинарником из официального образа — ставить его через pip не нужно
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv

# UV_COMPILE_BYTECODE: .pyc собираются при сборке образа, старт контейнера быстрее
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock ./
# --locked: собрать ровно то, что в uv.lock, иначе упасть
# --no-dev: pytest, mypy, black и ruff в образ не попадают
RUN uv sync --locked --no-dev


FROM python:3.13-slim

# PYTHONUNBUFFERED: логи uvicorn попадают в docker logs сразу, а не после буфера
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Приложение работает не от root: если кто-то выберется из процесса,
# прав на систему у него не будет
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app alembic.ini pyproject.toml ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app certs ./certs
COPY --chown=app:app src ./src
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app
EXPOSE 8000

ENTRYPOINT ["entrypoint.sh"]
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]

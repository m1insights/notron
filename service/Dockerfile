FROM python:3.14.2-slim AS build
WORKDIR /app
RUN pip install --no-cache-dir uv==0.9.22
COPY pyproject.toml uv.lock ./
COPY notron_service ./notron_service
COPY migrations ./migrations
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14.2-slim
RUN groupadd --gid 10001 notron && useradd --uid 10001 --gid notron --no-create-home notron
WORKDIR /app
COPY --from=build --chown=notron:notron /app/.venv /app/.venv
USER 10001:10001
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "notron_service.app:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers", "--limit-concurrency", "50", "--timeout-keep-alive", "5"]

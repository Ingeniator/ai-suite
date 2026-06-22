FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN uv sync --no-dev --no-editable

FROM python:3.13-slim AS runtime

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY entrypoint.py .

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 5030

CMD ["python", "-m", "entrypoint"]

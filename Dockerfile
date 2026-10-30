FROM ghcr.io/astral-sh/uv:0.11.17 AS uv
FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ARG UV_DEFAULT_INDEX=https://packagefeedproxy.microsoft.io/pypi/simple
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY ontology ./ontology
RUN uv sync --frozen --no-dev --all-extras
COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
COPY sources ./sources
COPY tests/golden ./tests/golden
RUN useradd --create-home --uid 10001 ekb && mkdir -p /data && chown ekb:ekb /data
ENV PATH="/app/.venv/bin:$PATH" EKB_DATA_DIR=/data PYTHONUNBUFFERED=1
USER ekb
EXPOSE 8000
CMD ["ekb", "web", "--host", "0.0.0.0"]

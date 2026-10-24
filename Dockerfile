FROM ghcr.io/astral-sh/uv:0.13.2 AS uv
FROM python:3.12-slim AS builder
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md LICENSE ./
COPY src ./src
ARG INSTALL_LANGCHAIN=false
RUN if [ "$INSTALL_LANGCHAIN" = "true" ]; then \
      uv sync --locked --no-dev --no-editable --extra langchain; \
    else \
      uv sync --locked --no-dev --no-editable; \
    fi

FROM python:3.12-slim
COPY --from=uv /uv /uvx /usr/local/bin/
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /home/app/.config/rosa-llm \
    && chown -R app:app /home/app/.config \
    && chmod 700 /home/app/.config/rosa-llm
WORKDIR /app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    ROSA_LLM_AUTH_DIR=/home/app/.config/rosa-llm \
    UV_PYTHON_DOWNLOADS=never
USER app
ENTRYPOINT ["/app/.venv/bin/rosa-llm"]
CMD ["status", "--json"]

FROM ghcr.io/astral-sh/uv:0.11.1 AS uv

FROM python:3.12-slim-bookworm AS app
COPY --from=uv /uv /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache
COPY src ./src
COPY config ./config
COPY desktop/notetypes ./desktop/notetypes
COPY data ./data
# The pipeline's prompt imports the canon and the golden set from the skill (CLAUDE.md: never duplicated).
COPY .claude/skills/mnemosyne-cards ./.claude/skills/mnemosyne-cards
RUN uv sync --frozen --no-dev --no-cache
# /cache holds the books' full-text index (a named volume, which takes this owner when Docker creates it).
RUN mkdir -p /cache && chown 1000:1000 /cache
# 1000:1000 owns the Helios Ledger on the Obelisk.
USER 1000:1000
EXPOSE 8110
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8110/health', timeout=4)"]
CMD ["mnemosyne", "serve"]

# Integration tests against the test sync user only. Adds the anki library to seed and inspect the
# test collection; the service image above never opens a collection itself.
FROM app AS integration
USER root
RUN uv pip install --python /opt/venv/bin/python --no-cache "anki>=26.9,<27"
COPY scripts/integration_p2.py ./scripts/integration_p2.py
COPY scripts/anki_apply.py ./scripts/anki_apply.py
USER 1000:1000
CMD ["python", "scripts/integration_p2.py"]

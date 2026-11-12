# Multi-arch runtime image for the self-hosted IMAP/SMTP MCP server.
# Build: docker buildx build --platform linux/amd64,linux/arm64 -t mcp-email-server .
#
# The virtualenv stays at /app/.venv so an overridden entrypoint can still
# address the interpreter directly.

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.11.29

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim AS builder

COPY --from=uv /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY uv.lock pyproject.toml README.md LICENSE ./
RUN uv sync --frozen --no-install-project --no-dev

COPY mcp_email_server ./mcp_email_server
RUN uv sync --frozen --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim AS runtime

LABEL org.opencontainers.image.description="Self-hosted multi-account IMAP/SMTP MCP server" \
      org.opencontainers.image.licenses="BSD-3-Clause"

# Upgrade base packages (zlib and friends) and drop pip/setuptools/wheel: the
# runtime process uses /app/.venv and does not need a packaging toolchain.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/* \
 && python -m pip uninstall -y --disable-pip-version-check pip setuptools wheel \
 && rm -rf /usr/local/lib/python*/ensurepip \
 && useradd --uid 1000 --create-home --home-dir /data mcp \
 && mkdir -p /data/cache /data/attachments /tmp \
 && chown -R mcp:mcp /data /tmp

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
RUN chown -R mcp:mcp /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    HOME=/data \
    TMPDIR=/tmp \
    MCP_EMAIL_SERVER_CONFIG_PATH=/data/config.toml \
    MCP_EMAIL_CACHE_DIR=/data/cache \
    MCP_EMAIL_SERVER_ATTACHMENT_DIR=/data/attachments

USER mcp

ENTRYPOINT ["tini", "--", "mcp-email-server"]
# Default remains stdio so container smoke tests and local clients keep working.
# docker-compose.example.yml overrides this to Streamable HTTP for NAS use.
CMD ["stdio"]

# One image per deployment (LLD §17.1). Base images pinned by digest; locked dependency set (LLD §3.5, §14.4).
# Digests are the ones llm-retrieval-generation pins (the worker imports the engine, so the Python must match: LLD §3.5).
# Build (the engine is a private git dependency, fetched over https with a token passed as a BuildKit
# secret and never baked into a layer; CI passes GO_PRIVATE_TOKEN, `make docker-build` uses it or `gh auth token`):
#   docker build --secret id=gh_token,env=GO_PRIVATE_TOKEN -f deploy/Dockerfile -t llm-workers-generation:dev .
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.15.2-slim-bookworm@sha256:4e03260adeeebafccd084f3584e6b84823aba5651cfb53ef8a078ed441ddf9cd AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY generation/pyproject.toml generation/pyproject.toml
COPY generation/src generation/src
# --no-editable: the runtime stage copies only the venv, so the package must be installed into it
RUN --mount=type=secret,id=gh_token \
    git config --global url."https://github.com/".insteadOf "ssh://git@github.com/" \
    && git config --global credential.helper '!f() { echo username=x-access-token; echo "password=$(cat /run/secrets/gh_token)"; }; f' \
    && uv sync --frozen --no-dev --no-editable --package llm-workers-generation \
    && git config --global --unset-all credential.helper

FROM python:3.15.2-slim-bookworm@sha256:4e03260adeeebafccd084f3584e6b84823aba5651cfb53ef8a078ed441ddf9cd
ARG BUILD_VERSION=dev
LABEL org.opencontainers.image.version=$BUILD_VERSION \
      org.opencontainers.image.source=https://github.com/BCBP-SOLUTIONS-FZC-LLC/llm-workers
# Security fixes the pinned base lacks: OS packages upgraded (apt, libpam), and pip removed (the worker never
# runs it; its vendored urllib3, msgpack and setuptools carry known CVEs). The service runs from /app/.venv.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.15/ensurepip/_bundled
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin worker
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 BUILD_ID=$BUILD_VERSION
USER 10001
# 8080: ops server (/health, /live, /ready, /metrics). 9464: the Temporal runtime (SDK telemetry and the workflow-side metrics). No other inbound port.
EXPOSE 8080 9464
ENTRYPOINT ["llm-workers-generation"]

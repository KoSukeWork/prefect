# syntax=docker/dockerfile:1
ARG NODE_VERSION=22.23.2
ARG PYTHON_VERSION=3.12
ARG CODE_SERVER_VERSION=4.138.0
FROM node:${NODE_VERSION}-bookworm-slim AS ui
WORKDIR /build/ui
COPY ui/package*.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --legacy-peer-deps
COPY ui/ ./
RUN npm run build

FROM node:${NODE_VERSION}-bookworm-slim AS ui-v2
WORKDIR /build/ui-v2
COPY ui-v2/package*.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --legacy-peer-deps
COPY ui-v2/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.12.10 AS uv
FROM python:${PYTHON_VERSION}-slim-trixie AS python-base
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates libstdc++6 libatomic1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /bin/bash app

FROM python-base AS python-build
WORKDIR /opt/prefect
COPY pyproject.toml uv.lock README.md LICENSE hatch_build.py ./
COPY tools/ tools/
COPY src/prefect/ src/prefect/
COPY .git/ .git/
COPY --from=ui /build/ui/dist src/prefect/server/ui/
COPY --from=ui-v2 /build/ui-v2/dist src/prefect/server/ui-v2/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN --mount=type=cache,target=/root/.cache/uv \
    PREFECT_REQUIRE_PACKAGED_UI_BUNDLES=1 uv sync --frozen --no-dev --no-editable
RUN --mount=type=cache,target=/root/.cache/uv uv build --wheel --out-dir /opt/prefect-dist

FROM python-base AS runtime
COPY --from=python-build /opt/venv /opt/venv
COPY --from=python-build /opt/prefect-dist /opt/prefect-dist
ENV PATH="/opt/workbench/bin:/opt/venv/bin:${PATH}" HOME=/home/app \
    PREFECT_HOME=/home/app/.prefect PREFECT_SERVER_ANALYTICS_ENABLED=false \
    PREFECT_CLI_PROMPT=false PYTHONUNBUFFERED=1
COPY deploy/workbench/seed/ /opt/workbench-seed/
COPY deploy/workbench/workbench.py /usr/local/bin/workbench
COPY deploy/workbench/profile.sh /etc/profile.d/workbench.sh
COPY deploy/workbench/manager /opt/workbench/manager
RUN mkdir -p /workspace /opt/workbench-env /opt/workbench/bin /home/app/.local/share/code-server /home/app/.prefect \
    && cp /opt/workbench/manager/python /opt/workbench/bin/python \
    && chmod +x /opt/workbench/bin/python \
    && chown -R app:app /workspace /home/app /opt/workbench-env
USER app
WORKDIR /workspace
CMD ["prefect", "version"]

FROM codercom/code-server:${CODE_SERVER_VERSION} AS code-server
FROM runtime AS editor
USER root
COPY --from=code-server /usr/lib/code-server /usr/lib/code-server
RUN ln -s /usr/lib/code-server/bin/code-server /usr/local/bin/code-server
ARG ZH_HANS_VERSION=1.131.0
RUN mkdir -p /opt/code-server-language \
    && python -c "import hashlib, urllib.request; base='https://open-vsx.org/api/MS-CEINTL/vscode-language-pack-zh-hans/${ZH_HANS_VERSION}/file/MS-CEINTL.vscode-language-pack-zh-hans-${ZH_HANS_VERSION}'; data=urllib.request.urlopen(base+'.vsix').read(); expected=urllib.request.urlopen(base+'.sha256').read().decode().split()[0]; assert hashlib.sha256(data).hexdigest() == expected; open('/opt/code-server-language/zh-hans.vsix','wb').write(data)"
COPY deploy/workbench/editor-entrypoint.sh /usr/local/bin/editor-entrypoint.sh
COPY deploy/workbench/editor-language.py /usr/local/bin/editor-language.py
USER app
ENTRYPOINT ["/bin/sh", "/usr/local/bin/editor-entrypoint.sh"]
CMD ["code-server", "--bind-addr", "0.0.0.0:8080", "--auth", "password", "--disable-telemetry", "--locale", "zh-cn", "/workspace"]

# One deployable container; components remain separate processes and source modules.
FROM editor AS standalone
USER root
RUN apt-get update && apt-get install -y --no-install-recommends nginx postgresql-17 \
    && (pg_dropcluster --stop 17 main || true) \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/lib/workbench-postgres /tmp/workbench-postgres \
    && chown app:app /var/lib/workbench-postgres /tmp/workbench-postgres
COPY deploy/workbench/standalone /opt/workbench/standalone
USER app
EXPOSE 8090
ENTRYPOINT ["/opt/venv/bin/python", "/opt/workbench/standalone/supervisor.py"]
CMD []

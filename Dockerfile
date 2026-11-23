# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SQUARE_HARNESS_CONTAINER=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE.md NOTICE ./
COPY mathagent ./mathagent
COPY deployment/docker-launch.py /usr/local/bin/square-harness-launch
RUN python -m pip install --no-cache-dir '.[research,gcp-secrets]' \
    && mkdir -p /data/local/workspaces/default /data/server \
    && chown -R 10001:10001 /data

COPY deployment/docker-entrypoint.sh /usr/local/bin/square-harness-entrypoint
RUN chmod 0555 /usr/local/bin/square-harness-entrypoint

ENV SQUARE_DATA_DIR=/data/local \
    SQUARE_SERVER_DATA_ROOT=/data/server
VOLUME ["/data"]
EXPOSE 8765
USER root
ENTRYPOINT ["/usr/local/bin/square-harness-entrypoint"]

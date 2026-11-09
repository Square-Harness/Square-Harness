# syntax=docker/dockerfile:1
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 SquareHarness Team

# Square Harness Python sandbox (shared by python-lab and numerical-sanity-check).
#
# Build (current architecture):
#   python -m mathagent.numerics build
# or equivalently:
#   docker build -t square-harness/python-numerics:2 mathagent/numerics/docker
# Multi-architecture build (linux/amd64 + linux/arm64):
#   docker buildx build --platform linux/amd64,linux/arm64 -t <registry>/python-numerics:2 --push mathagent/numerics/docker
#
# The base image is pinned by its multi-architecture index digest and every wheel by
# its hash, so a rebuild installs the same files. Nothing is installed at run time:
# the harness starts containers with --network none and a read-only root filesystem.
FROM python:3.12.13-slim-trixie@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

COPY requirements.lock /tmp/requirements.lock
RUN python -m pip install --require-hashes --only-binary=:all: --no-deps -r /tmp/requirements.lock \
 && rm /tmp/requirements.lock \
 && python -m pip uninstall -y pip \
 && useradd --uid 10001 --user-group --no-create-home --home-dir /tmp --shell /usr/sbin/nologin sandbox \
 && find / -xdev -perm /6000 -type f -exec chmod a-s {} +

# Matplotlib font cache built once, copied to the writable /tmp at start (read-only root).
COPY runner.py /opt/sandbox/runner.py
RUN mkdir -p /opt/sandbox/mplconfig \
 && MPLCONFIGDIR=/opt/sandbox/mplconfig MPLBACKEND=Agg python -c "import matplotlib.pyplot as plt; plt.figure(); plt.close('all')" \
 && python -m compileall -q /opt/sandbox \
 && chmod -R a+rX /opt/sandbox

LABEL org.squareharness.sandbox="python-numerics" \
      org.squareharness.sandbox.version="2" \
      org.opencontainers.image.title="Square Harness Python sandbox" \
      org.opencontainers.image.licenses="Apache-2.0"

USER 10001:10001
WORKDIR /tmp
# -s: no user site-packages; -P: no script directory on sys.path. The environment is set
# only by this image and the harness (host variables are never passed in).
ENTRYPOINT ["python", "-s", "-P", "/opt/sandbox/runner.py"]

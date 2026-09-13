# syntax=docker/dockerfile:1

# Alpine rather than Debian slim, chosen for security rather than size.
#
# When this was measured, `python:3.12-slim` carried 3 critical and 53 high
# findings, only 12 of which had a fix available -- the rest are Debian packages
# marked `affected` or `will_not_fix`, so no amount of patching clears them.
# The Alpine image carried 0 critical and 7 high, every one fixable by the
# `apk upgrade` below. Re-check with `make scan-image` before changing this.
ARG PYTHON_VERSION=3.12
ARG BASE_IMAGE=python:${PYTHON_VERSION}-alpine

# ---- build stage: install the package into a venv ----
FROM ${BASE_IMAGE} AS build

# Upgrade first, so the build stage cannot bake a known-vulnerable library into
# anything it compiles.
RUN apk upgrade --no-cache

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

# Compilers are needed only when a dependency has no musl wheel. They live in
# this stage and never reach the runtime image.
RUN apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install --no-cache-dir ".[mock]" \
    && apk del .build-deps \
    # Strip the installer once installing is done. Even the newest pip vendors
    # its own copies of msgpack and pkg_resources, and those vendored copies
    # were the only findings left in the scanned image. Nothing at runtime
    # imports them: entry points are discovered through importlib.metadata,
    # which is standard library.
    && /opt/venv/bin/pip uninstall -y pip setuptools wheel 2>/dev/null || true \
    && rm -rf /opt/venv/lib/python3*/site-packages/pip \
    /opt/venv/lib/python3*/site-packages/pip-*.dist-info \
    /opt/venv/lib/python3*/site-packages/setuptools \
    /opt/venv/lib/python3*/site-packages/setuptools-*.dist-info \
    /opt/venv/lib/python3*/site-packages/pkg_resources \
    /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.* \
    && find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} +

# ---- runtime stage: minimal image with just the venv ----
FROM ${BASE_IMAGE} AS runtime

# Patch the runtime's own packages. Without this the image ships whatever the
# base tag was built with, however long ago that was.
RUN apk upgrade --no-cache

# Drop the interpreter's own pip. The application runs entirely from /opt/venv,
# so this copy is never used -- but its vendored libraries (msgpack,
# pkg_resources) are still scanned, and were the only findings left in the
# image. A runtime image has no business carrying a package installer anyway.
RUN rm -rf /usr/local/lib/python3*/site-packages/pip \
    /usr/local/lib/python3*/site-packages/pip-*.dist-info \
    /usr/local/lib/python3*/ensurepip \
    /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.*

# Run as a non-root user. Alpine ships adduser, not Debian's useradd.
RUN adduser -D -u 10001 phantom
COPY --from=build --chown=phantom:phantom /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Bind to all interfaces inside the container (an explicit opt-in there).
    # The port is deliberately NOT set: it would override the port in a mock's
    # own config file, and a container-wide default has no business doing that.
    # `serve` defaults to 3000 and `mock serve` to its config, usually 8443.
    PHANTOM_API_HOST=0.0.0.0

# Specs, configs and corpora are mounted here at runtime.
WORKDIR /spec
USER phantom

# 3000 serves spec-driven mocks; 8443 serves `phantom-api mock` over TLS.
EXPOSE 3000 8443

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["phantom-api", "--version"]

ENTRYPOINT ["phantom-api"]
# Default command prints help. Override to serve something, e.g.:
#   docker run -p 3000:3000 -v $PWD/openapi.json:/spec/openapi.json \
#     phantom-api serve /spec/openapi.json
#   docker run -p 8443:8443 -v $PWD/examples/vcenter:/spec \
#     phantom-api mock serve /spec/phantom.yaml
CMD ["--help"]

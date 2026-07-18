# syntax=docker/dockerfile:1

# ---- build stage: install the package into a venv ----
FROM python:3.12-slim AS build

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir .

# ---- runtime stage: minimal image with just the venv ----
FROM python:3.12-slim AS runtime

# Run as a non-root user.
RUN useradd --create-home --uid 10001 phantom
COPY --from=build /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    # Bind to all interfaces inside the container (explicit opt-in for containers).
    PHANTOM_API_HOST=0.0.0.0 \
    PHANTOM_API_PORT=3000

# Specs are mounted here at runtime, e.g. -v $PWD/spec.yaml:/spec/openapi.yaml
WORKDIR /spec
USER phantom

EXPOSE 3000

ENTRYPOINT ["phantom-api"]
# Default command prints help; override to serve a spec, e.g.:
#   docker run -p 3000:3000 -v $PWD/openapi.json:/spec/openapi.json \
#     fazorboy/phantom-api serve /spec/openapi.json
CMD ["--help"]

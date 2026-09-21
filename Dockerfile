# ── Build stage: install dependencies ───────────────────────────────────────
# Use the same Python version as the runtime so compiled extensions match.
FROM python:3.13-slim AS builder

WORKDIR /install

# pip needs gcc for a couple of transitive deps (e.g. cryptography); install
# only what is necessary, then discard this layer in the final stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /requirements.txt
RUN pip install --prefix=/install -r /requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.13-slim

WORKDIR /app

# Copy installed packages from the build stage.
COPY --from=builder /install /usr/local

# Copy application source.
COPY . .

# Create persistent-data directories so they survive bind-mount-less runs.
RUN mkdir -p data logs

# Dashboard port.
EXPOSE 5123

# run.py starts both the bot and the API server, applies schema migrations,
# runs preflight checks, and handles SIGINT/SIGTERM cleanly.
# --no-browser: there is no display inside a container.
# --host 0.0.0.0: bind to all interfaces so Docker port-mapping works.
CMD ["python", "run.py", "--no-browser", "--host", "0.0.0.0"]

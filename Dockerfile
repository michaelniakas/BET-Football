# Frontend: types generated from openapi.yaml, typechecked and built to static files.
FROM node:26-slim@sha256:5adad64f9a1fca2d51972caa65983634c8a774eb300d72b20f7f2a0f60368072 AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY openapi.yaml /src/
COPY web/ ./
RUN npm run build

# Runtime: Python only. Every Python dependency installs from a prebuilt wheel, so a separate
# Python build stage would leave no toolchain behind and only cost a ~1 GB layer copy.
FROM python:3.11-slim@sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --uid 10001 --no-create-home app
# The worker's volume mounts here (WORKER_DIR=/data): a new volume takes this directory's owner.
RUN install -d -o app -g app /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY openapi.yaml .
COPY api/ api/
COPY model/ model/
COPY ingest/ ingest/
COPY --from=web /src/web/dist web/dist
USER app
EXPOSE 7860
# The worker (WORKER_DIR set) serves nothing: alive = its process is running, and a crash restarts it.
# ponytail: a hung worker still reports healthy; a heartbeat file checked here if that ever happens.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD [ -n "$WORKER_DIR" ] || python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/health', timeout=4)"
# One process; request concurrency comes from FastAPI's threadpool (the handlers are sync).
CMD ["uvicorn", "--factory", "api.main:from_env", "--host", "0.0.0.0", "--port", "7860"]

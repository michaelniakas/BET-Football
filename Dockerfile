# Single stage on purpose: every dependency installs from a prebuilt wheel, so a
# build stage would leave no toolchain behind and only cost a ~1 GB layer copy.
FROM python:3.11-slim@sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --uid 10001 --no-create-home app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# .dockerignore allowlists the app files, so this copies only api/, model/ and openapi.yaml.
COPY . .
USER app
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/health', timeout=4)"
# One process; request concurrency comes from FastAPI's threadpool (the handlers are sync).
CMD ["uvicorn", "--factory", "api.main:from_env", "--host", "0.0.0.0", "--port", "7860"]

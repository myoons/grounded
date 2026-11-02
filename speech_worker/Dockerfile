FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python PATH="/opt/venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libgomp1 && rm -rf /var/lib/apt/lists/*
RUN uv python install 3.12.13 && uv venv --python 3.12.13 /opt/venv
WORKDIR /app
COPY speech_worker/requirements.lock /app/requirements.lock
RUN uv pip install --python /opt/venv/bin/python --no-cache --require-hashes -r /app/requirements.lock
COPY speech_worker/__init__.py speech_worker/download.py speech_worker/engine.py speech_worker/handler.py /app/speech_worker/
RUN python3 -m speech_worker.download
ENV HF_HUB_OFFLINE=1
CMD ["python3", "-m", "speech_worker.handler"]

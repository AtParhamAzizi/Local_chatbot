FROM python:3.12-slim-bookworm

ARG INSTALL_PROFILE=cpu

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/home/chatbot/.cache/huggingface \
    APP_ENV=production \
    APP_HOST=0.0.0.0

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv "$VIRTUAL_ENV" \
    && python -m pip install --upgrade pip \
    && useradd --create-home --user-group --uid 10001 chatbot

COPY requirements*.txt ./
COPY backend/STT/requirements.txt ./backend/STT/requirements.txt

RUN case "$INSTALL_PROFILE" in \
        cpu) python -m pip install 'torch>=2.4' --index-url https://download.pytorch.org/whl/cpu \
             && python -m pip install -r requirements-docker.txt ;; \
        cuda) python -m pip install -r requirements-docker.txt -r requirements-cuda.txt ;; \
        *) echo 'INSTALL_PROFILE must be cpu or cuda' >&2; exit 1 ;; \
    esac \
    && python -m pip check

COPY --chown=chatbot:chatbot backend/ ./backend/
COPY --chown=chatbot:chatbot frontend/ ./frontend/

RUN mkdir -p backend/model backend/modelgen backend/STT/.models "$HF_HOME" \
    && chown -R chatbot:chatbot /app /home/chatbot/.cache

USER chatbot

EXPOSE 5000 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=4).close()"

CMD ["python", "backend/main.py"]

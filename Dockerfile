FROM python:3.12-slim-bookworm

ARG INSTALL_PROFILE=cpu
ARG INSTALL_OCR=0

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
    && case "$INSTALL_OCR" in \
         1) apt-get install --no-install-recommends -y tesseract-ocr tesseract-ocr-fas ;; \
         0) ;; \
         *) echo 'INSTALL_OCR must be 0 or 1' >&2; exit 1 ;; \
       esac \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv "$VIRTUAL_ENV" \
    && useradd --create-home --user-group --uid 10001 chatbot

COPY requirements*.txt ./
COPY requirements/ ./requirements/
COPY scripts/dependencies.py ./scripts/dependencies.py
COPY backend/STT/requirements.txt ./backend/STT/requirements.txt

RUN case "$INSTALL_PROFILE" in \
        cpu) if [ "$(uname -m)" = x86_64 ]; then \
                 python scripts/dependencies.py install --profile cpu \
                 && python scripts/dependencies.py verify --profile cpu; \
             else \
                 echo 'ARM64 CPU uses version constraints, not the tested x86-64 artifact locks' \
                 && python -m pip install 'torch==2.7.1' --index-url https://download.pytorch.org/whl/cpu \
                 && python -m pip install -c requirements/constraints.txt -r requirements-docker.txt; \
             fi ;; \
        cuda) python scripts/dependencies.py install --profile cu118 \
              && python scripts/dependencies.py verify --profile cu118 --software-only ;; \
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

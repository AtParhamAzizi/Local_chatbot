"""Realtime speech-to-text support for the user chat service.

The implementation reuses the PCM framing and voice-activity logic from the
standalone ``backend/STT`` project, but exposes it on the chatbot's existing
Flask application so the browser only needs one origin and one server.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import jsonify, request, send_from_directory

from abuse_controls import check_request_limit, limit_requests, rate_limited_response, retry_message

from STT.streaming import AudioStream, MAX_PACKET_BYTES, SAMPLE_RATE

try:
    from faster_whisper import WhisperModel
    from flask_sock import Sock
    from simple_websocket import ConnectionClosed
except (ImportError, OSError) as error:
    WhisperModel = None
    Sock = None
    ConnectionClosed = Exception
    IMPORT_ERROR = error
else:
    IMPORT_ERROR = None


BASE_DIR = Path(__file__).resolve().parent
STT_DIR = BASE_DIR / "STT"
MODEL_NAME = os.environ.get("STT_MODEL", "base")
DEVICE = os.environ.get("STT_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get(
    "STT_COMPUTE_TYPE", "float16" if DEVICE == "cuda" else "int8"
)
LANGUAGE = os.environ.get("STT_LANGUAGE", "fa")
VOICE_THRESHOLD = float(os.environ.get("STT_VOICE_THRESHOLD", "0.008"))
MAX_SESSION_SECONDS = 120
MAX_SESSION_AUDIO_BYTES = MAX_SESSION_SECONDS * SAMPLE_RATE * 2

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
_model_lock = threading.Lock()
_transcription_slot = threading.BoundedSemaphore(1)
_model_future = None


def _load_model():
    if WhisperModel is None:
        raise RuntimeError(
            "Voice dependencies are not installed. Run: "
            "python -m pip install -r requirements.txt"
        )
    logging.getLogger(__name__).info(
        "Loading Whisper %s on %s (%s)", MODEL_NAME, DEVICE, COMPUTE_TYPE
    )
    return WhisperModel(
        MODEL_NAME,
        device=DEVICE,
        compute_type=COMPUTE_TYPE,
        cpu_threads=int(os.environ.get("STT_CPU_THREADS", min(4, os.cpu_count() or 1))),
        num_workers=1,
        download_root=str(STT_DIR / ".models"),
        local_files_only=os.environ.get("STT_LOCAL_FILES_ONLY") == "1",
    )


def _start_model_loading():
    global _model_future
    if _model_future is not None:
        return
    with _model_lock:
        if _model_future is None:
            _model_future = _executor.submit(_load_model)


def _model_status():
    if IMPORT_ERROR is not None:
        return "unavailable", "Voice dependencies are unavailable."
    if _model_future is None or not _model_future.done():
        return "loading", None
    error = _model_future.exception()
    return ("error", "Voice model failed to load.") if error else ("ready", None)


def _transcribe_audio(audio):
    segments, _ = _model_future.result().transcribe(
        audio,
        language=None if LANGUAGE == "auto" else LANGUAGE,
        task="transcribe",
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters={
            "min_speech_duration_ms": 100,
            "min_silence_duration_ms": 300,
            "speech_pad_ms": 200,
        },
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


def _release_slot(future):
    if future is not None and not future.done():
        future.add_done_callback(lambda completed: _transcription_slot.release())
    else:
        _transcription_slot.release()


def configure_voice(app):
    """Register voice endpoints without blocking text chat on model loading."""
    if "voice" in app.extensions:
        return
    app.extensions["voice"] = True

    @app.get("/voice/audio-worklet.js")
    def voice_worklet():
        return send_from_directory(STT_DIR, "audio-worklet.js")

    @app.get("/voice/health")
    @limit_requests('voice_health')
    def voice_health():
        if IMPORT_ERROR is None:
            _start_model_loading()
        status, error = _model_status()
        return jsonify(
            status=status,
            error=(
                "Voice dependencies are unavailable. Run: "
                "python -m pip install -r requirements.txt. " + error
                if status == "unavailable" else error
            ),
            model=MODEL_NAME,
            device=DEVICE,
            language=LANGUAGE,
            sample_rate=SAMPLE_RATE,
        ), 200 if status == "ready" else 503

    if Sock is None:
        return

    options = app.config.setdefault("SOCK_SERVER_OPTIONS", {})
    options.setdefault("ping_interval", 25)
    options.setdefault("max_message_size", MAX_PACKET_BYTES)
    sock = Sock(app)

    @app.before_request
    def limit_voice_handshake():
        if request.path == '/voice-stream' and request.method == 'GET':
            retry_after = check_request_limit('voice')
            if retry_after:
                return rate_limited_response(retry_after)
        return None

    @sock.route("/voice-stream")
    def voice_stream(websocket):
        status, error = _model_status()
        if status != "ready":
            websocket.send(json.dumps({"type": "error", "message": error or "Model is loading."}))
            return
        if not _transcription_slot.acquire(blocking=False):
            websocket.send(json.dumps({
                "type": "error", "code": "rate_limited", "retry_after": 5,
                "message": "Another transcription is active. Please retry in 5 seconds.",
            }))
            return

        future = None
        job = None
        try:
            audio_stream = AudioStream(voice_threshold=VOICE_THRESHOLD)
            stopping = False
            started = last_received = time.monotonic()
            audio_bytes = 0
            websocket.send(json.dumps({"type": "ready", "sample_rate": SAMPLE_RATE}))

            while True:
                if time.monotonic() - started >= MAX_SESSION_SECONDS:
                    websocket.send(json.dumps({
                        "type": "error", "code": "rate_limited", "retry_after": 5,
                        "message": "Recording time limit reached. " + retry_message(5),
                    }))
                    return
                if future is not None and future.done():
                    websocket.send(json.dumps({
                        "type": "transcript",
                        "segment_id": job.segment_id,
                        "text": future.result(),
                        "is_final": job.is_final,
                    }))
                    future = None

                if future is None:
                    job = audio_stream.next_job()
                    if job is not None:
                        future = _executor.submit(_transcribe_audio, job.audio)

                if stopping and future is None and not audio_stream.has_pending:
                    websocket.send(json.dumps({"type": "done"}))
                    return

                message = websocket.receive(timeout=0.05)
                if message is None:
                    if not stopping and time.monotonic() - last_received > 30:
                        raise ValueError("No audio received for 30 seconds. Start a new recording.")
                    continue

                last_received = time.monotonic()
                if isinstance(message, bytes):
                    if stopping:
                        raise ValueError("Audio received after stop.")
                    if len(message) > MAX_PACKET_BYTES:
                        raise ValueError("Audio packet is too large.")
                    audio_bytes += len(message)
                    if audio_bytes > MAX_SESSION_AUDIO_BYTES:
                        websocket.send(json.dumps({
                            "type": "error", "code": "rate_limited", "retry_after": 5,
                            "message": "Recording audio limit reached. " + retry_message(5),
                        }))
                        return
                    audio_stream.feed(message)
                else:
                    command = json.loads(message)
                    if not isinstance(command, dict) or command.get("type") != "stop":
                        raise ValueError("Expected PCM audio or a stop message.")
                    if not stopping:
                        audio_stream.finish()
                        stopping = True

        except ConnectionClosed:
            pass
        except Exception as error:
            logging.getLogger(__name__).error("Streaming transcription failed (%s)", type(error).__name__)
            try:
                websocket.send(json.dumps({"type": "error", "message": "Transcription failed. Please try again."}))
            except ConnectionClosed:
                pass
        finally:
            _release_slot(future)

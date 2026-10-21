import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio
from flask import Flask, abort, jsonify, request, send_from_directory
from flask_sock import Sock
from simple_websocket import ConnectionClosed

from streaming import AudioStream, MAX_PACKET_BYTES, SAMPLE_RATE


BASE_DIR = Path(__file__).resolve().parent
MODEL_NAME = os.environ.get("STT_MODEL", "base")
DEVICE = os.environ.get("STT_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get(
    "STT_COMPUTE_TYPE", "float16" if DEVICE == "cuda" else "int8"
)
LANGUAGE = os.environ.get("STT_LANGUAGE", "fa")
VOICE_THRESHOLD = float(os.environ.get("STT_VOICE_THRESHOLD", "0.008"))

app = Flask(__name__, static_folder=None)
app.config.update(
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
    SOCK_SERVER_OPTIONS={"ping_interval": 25, "max_message_size": MAX_PACKET_BYTES},
)
sock = Sock(app)
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
model_lock = threading.Lock()
transcription_slot = threading.BoundedSemaphore(1)
model_future = None


def load_model():
    app.logger.info("Loading Whisper %s on %s (%s)", MODEL_NAME, DEVICE, COMPUTE_TYPE)
    return WhisperModel(
        MODEL_NAME,
        device=DEVICE,
        compute_type=COMPUTE_TYPE,
        cpu_threads=int(os.environ.get("STT_CPU_THREADS", min(4, os.cpu_count() or 1))),
        num_workers=1,
        download_root=str(BASE_DIR / ".models"),
        local_files_only=os.environ.get("STT_LOCAL_FILES_ONLY") == "1",
    )


@app.before_request
def start_model_loading():
    global model_future
    with model_lock:
        if model_future is None:
            model_future = executor.submit(load_model)


def model_status():
    if model_future is None or not model_future.done():
        return "loading", None
    error = model_future.exception()
    return ("error", str(error)) if error else ("ready", None)


def transcribe_audio(audio):
    segments, _ = model_future.result().transcribe(
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


def release_slot(future):
    if future is not None and not future.done():
        future.add_done_callback(lambda completed: transcription_slot.release())
    else:
        transcription_slot.release()


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/<path:filename>")
def static_asset(filename):
    if filename not in {"script.js", "style.css", "audio-worklet.js"}:
        abort(404)
    return send_from_directory(BASE_DIR, filename)


@app.get("/health")
def health():
    status, error = model_status()
    return jsonify(
        status=status,
        error=error,
        model=MODEL_NAME,
        device=DEVICE,
        language=LANGUAGE,
        sample_rate=SAMPLE_RATE,
    ), 200 if status == "ready" else 503


@sock.route("/stream")
def stream_audio(websocket):
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.host:
        websocket.close(reason=1008, message="Use the page served by this server.")
        return

    status, error = model_status()
    if status != "ready":
        websocket.send(json.dumps({"type": "error", "message": error or "Model is loading."}))
        return
    if not transcription_slot.acquire(blocking=False):
        websocket.send(json.dumps({"type": "error", "message": "Another transcription is active."}))
        return

    future = None
    job = None
    try:
        audio_stream = AudioStream(voice_threshold=VOICE_THRESHOLD)
        stopping = False
        last_received = time.monotonic()
        websocket.send(json.dumps({"type": "ready", "sample_rate": SAMPLE_RATE}))

        while True:
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
                    future = executor.submit(transcribe_audio, job.audio)

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
        app.logger.exception("Streaming transcription failed")
        try:
            websocket.send(json.dumps({"type": "error", "message": str(error)}))
        except ConnectionClosed:
            pass
    finally:
        release_slot(future)


@app.post("/voice")
def voice():
    if "audio" not in request.files:
        return jsonify(error="No audio file provided."), 400
    status, error = model_status()
    if status != "ready":
        return jsonify(error=error or "Model is loading."), 503
    if not transcription_slot.acquire(blocking=False):
        return jsonify(error="Another transcription is active."), 429

    future = None
    try:
        try:
            audio = decode_audio(request.files["audio"].stream, sampling_rate=SAMPLE_RATE)
        except Exception:
            return jsonify(error="The uploaded file could not be decoded as audio."), 400
        if not audio.size or audio.size > SAMPLE_RATE * 60:
            return jsonify(error="Audio must contain between 0 and 60 seconds of sound."), 400
        future = executor.submit(transcribe_audio, audio)
        transcript = future.result()
        return jsonify(transcript=transcript, reply=transcript, recommended_questions=[])
    except Exception:
        app.logger.exception("Uploaded audio transcription failed")
        return jsonify(error="Transcription failed. Check the server log."), 500
    finally:
        release_slot(future)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start_model_loading()
    app.run(
        host=os.environ.get("STT_HOST", "127.0.0.1"),
        port=int(os.environ.get("STT_PORT", "5000")),
        threaded=True,
        debug=False,
    )

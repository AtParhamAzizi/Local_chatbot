from __future__ import annotations

import io
import math
import os
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

if __package__:
    from .tts_engine import ModelLoadError, SpeechEngine
else:
    from tts_engine import ModelLoadError, SpeechEngine


BASE_DIR = Path(__file__).resolve().parent


def create_app(engine_factory=SpeechEngine):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    engine = None
    startup_error = None
    lock = threading.Lock()

    def get_engine():
        nonlocal engine, startup_error
        with lock:
            if engine is None and startup_error is None:
                try:
                    engine = engine_factory(BASE_DIR)
                except Exception as error:
                    app.logger.exception("TTS initialization failed")
                    startup_error = str(error)
            if engine is None:
                raise ModelLoadError(startup_error or "Speech models are unavailable.")
            return engine

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/api/status")
    def status():
        try:
            return jsonify(get_engine().status())
        except ModelLoadError as error:
            return jsonify({"ready": False, "error": str(error)}), 503

    @app.post("/api/synthesize")
    def synthesize():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Please send a JSON object."}), 400
        text = payload.get("text")
        voice = payload.get("voice", os.getenv("ENGLISH_VOICE", "af_heart"))
        speed = payload.get("speed", 1.0)
        if not isinstance(text, str) or not text.strip():
            return jsonify({"error": "Please enter some text."}), 400
        text = text.strip()
        if len(text) > 5_000:
            return jsonify({"error": "Please keep the text under 5,000 characters."}), 400
        if not isinstance(voice, str) or not voice.strip():
            return jsonify({"error": "Voice must be a nonempty string."}), 400
        if isinstance(speed, bool) or not isinstance(speed, (int, float, str)):
            return jsonify({"error": "Speed must be a number."}), 400
        try:
            speed = float(speed)
        except (ValueError, OverflowError):
            return jsonify({"error": "Speed must be a number."}), 400
        if not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
            return jsonify({"error": "Speed must be between 0.5 and 2.0."}), 400

        try:
            audio, sample_rate, segment_count = get_engine().synthesize(text, voice, speed)
        except ModelLoadError as error:
            return jsonify({"error": str(error)}), 503
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception:
            app.logger.exception("TTS generation failed")
            return jsonify({"error": "Speech generation failed."}), 500

        from scipy.io import wavfile

        output = io.BytesIO()
        wavfile.write(output, sample_rate, audio)
        output.seek(0)
        response = send_file(output, mimetype="audio/wav", download_name="persian-english-speech.wav")
        response.headers["X-TTS-Segments"] = str(segment_count)
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")), debug=False)

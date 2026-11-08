from __future__ import annotations

import io
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from tts_engine import ModelLoadError, SpeechEngine


BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder="templates", static_folder="static")

try:
    engine = SpeechEngine(BASE_DIR)
    startup_error = None
except Exception as error:  # Keep the UI available so setup errors are visible in the browser.
    engine = None
    startup_error = str(error)


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/status")
def status():
    if engine is None:
        return jsonify({"ready": False, "error": startup_error}), 503
    return jsonify(engine.status())


@app.post("/api/synthesize")
def synthesize():
    if engine is None:
        return jsonify({"error": startup_error}), 503

    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()
    voice = str(payload.get("voice", os.getenv("ENGLISH_VOICE", "af_heart")))

    try:
        speed = float(payload.get("speed", 1.0))
    except (TypeError, ValueError):
        return jsonify({"error": "Speed must be a number."}), 400

    if not text:
        return jsonify({"error": "Please enter some text."}), 400
    if len(text) > 5_000:
        return jsonify({"error": "Please keep the text under 5,000 characters."}), 400
    if not 0.5 <= speed <= 2.0:
        return jsonify({"error": "Speed must be between 0.5 and 2.0."}), 400

    try:
        audio, sample_rate, segment_count = engine.synthesize(text, voice, speed)
    except ModelLoadError as error:
        return jsonify({"error": str(error)}), 503
    except Exception as error:
        app.logger.exception("TTS generation failed")
        return jsonify({"error": f"Speech generation failed: {error}"}), 500

    from scipy.io import wavfile

    output = io.BytesIO()
    wavfile.write(output, sample_rate, audio)
    output.seek(0)
    response = send_file(output, mimetype="audio/wav", download_name="persian-english-speech.wav")
    response.headers["X-TTS-Segments"] = str(segment_count)
    return response


if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")), debug=False)

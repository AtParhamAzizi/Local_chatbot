
# backend/main.py

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from engine import get_engine


# ============================================================
# Paths
# ============================================================

# Project root:
# project/
# ├── backend/
# │   ├── main.py
# │   └── engine.py
# ├── frontend/
# │   └── index.html
# └── voices/

BASE_DIR = Path(__file__).resolve().parent.parent

BACKEND_DIR = BASE_DIR / "backend"
FRONTEND_DIR = BASE_DIR / "frontend"
VOICES_DIR = BASE_DIR / "voices"

VOICES_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# Server configuration
# ============================================================

ADMIN_HOST = "0.0.0.0"
ADMIN_PORT = 8000


# ============================================================
# Upload configuration
# ============================================================

ALLOWED_VOICE_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
}

# Maximum uploaded voice size: 100 MB
MAX_VOICE_SIZE = 100 * 1024 * 1024


# ============================================================
# Helpers
# ============================================================

def safe_voice_path(filename: str) -> Path:
    """
    Resolve a voice filename safely inside VOICES_DIR.

    Prevents paths such as:
        ../../file.wav
        ../something.wav
    """

    filename = Path(filename).name

    if not filename:
        raise HTTPException(
            status_code=400,
            detail="Invalid voice filename",
        )

    path = (VOICES_DIR / filename).resolve()

    try:
        path.relative_to(VOICES_DIR.resolve())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid voice path",
        )

    return path


def validate_voice_extension(filename: str) -> str:
    """
    Validate and return the lowercase extension.
    """

    extension = Path(filename).suffix.lower()

    if extension not in ALLOWED_VOICE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported audio format. "
                "Allowed formats: WAV, MP3, FLAC, OGG."
            ),
        )

    return extension


# ============================================================
# Lifespan
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Load the TTS engine once when FastAPI starts.
    """

    print("[main] Loading TTS engine...")

    try:
        get_engine()
        print("[main] TTS engine ready.")
    except Exception as exc:
        print(f"[main] Failed to load TTS engine: {exc}")
        raise

    yield

    print("[main] Shutting down...")


# ============================================================
# FastAPI application
# ============================================================

app = FastAPI(
    title="Pocket-TTS Farsi Web",
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Health
# ============================================================

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "Pocket-TTS Farsi Web",
    }


# ============================================================
# Voices
# ============================================================

@app.get("/api/voices")
def list_voices():
    """
    Return all supported voice files.
    """

    voices = sorted(
        p.name
        for p in VOICES_DIR.iterdir()
        if p.is_file()
        and p.suffix.lower() in ALLOWED_VOICE_EXTENSIONS
    )

    return {
        "voices": voices,
        "count": len(voices),
    }


@app.post("/api/voices")
async def upload_voice(file: UploadFile = File(...)):
    """
    Upload a voice file.

    Supported:
        WAV
        MP3
        FLAC
        OGG
    """

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Filename is required",
        )

    extension = validate_voice_extension(file.filename)

    # Generate our own filename.
    # This avoids collisions and unsafe filenames.
    filename = f"{uuid.uuid4().hex[:12]}{extension}"

    destination = VOICES_DIR / filename

    total_size = 0

    try:
        with destination.open("wb") as output:

            while True:
                chunk = await file.read(1024 * 1024)

                if not chunk:
                    break

                total_size += len(chunk)

                if total_size > MAX_VOICE_SIZE:
                    destination.unlink(missing_ok=True)

                    raise HTTPException(
                        status_code=413,
                        detail="Voice file is too large. Maximum size is 100 MB.",
                    )

                output.write(chunk)

    except HTTPException:
        raise

    except Exception as exc:
        destination.unlink(missing_ok=True)

        raise HTTPException(
            status_code=500,
            detail=f"Could not save voice file: {exc}",
        )

    finally:
        await file.close()

    return {
        "success": True,
        "filename": filename,
        "size": total_size,
    }


# ============================================================
# Synthesis
# ============================================================

@app.post("/api/synthesize")
async def synthesize(
    text: str = Form(...),
    voice: str = Form(...),
):
    """
    Generate WAV audio from Persian text.
    """

    # --------------------------------------------------------
    # Validate text
    # --------------------------------------------------------

    if not text or not text.strip():
        raise HTTPException(
            status_code=400,
            detail="Text cannot be empty",
        )

    text = text.strip()

    # --------------------------------------------------------
    # Validate voice
    # --------------------------------------------------------

    if not voice or not voice.strip():
        raise HTTPException(
            status_code=400,
            detail="Voice is required",
        )

    voice = Path(voice).name

    voice_path = safe_voice_path(voice)

    if not voice_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Voice '{voice}' not found",
        )

    if not voice_path.is_file():
        raise HTTPException(
            status_code=400,
            detail="Selected voice is not a valid file",
        )

    if voice_path.suffix.lower() not in ALLOWED_VOICE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported voice format",
        )

    # --------------------------------------------------------
    # Synthesize
    # --------------------------------------------------------

    try:
        engine = get_engine()

        wav = engine.synthesize(
            text,
            voice_path,
        )

    except Exception as exc:
        # Log the actual exception on the server.
        print(f"[main] Synthesis error: {exc}")

        raise HTTPException(
            status_code=500,
            detail="Synthesis failed",
        )

    # --------------------------------------------------------
    # Validate result
    # --------------------------------------------------------

    if wav is None:
        raise HTTPException(
            status_code=500,
            detail="TTS engine returned no audio",
        )

    if not isinstance(wav, (bytes, bytearray)):
        raise HTTPException(
            status_code=500,
            detail="TTS engine returned an invalid audio object",
        )

    if len(wav) == 0:
        raise HTTPException(
            status_code=500,
            detail="Generated audio is empty",
        )

    # --------------------------------------------------------
    # Return WAV
    # --------------------------------------------------------

    return Response(
        content=bytes(wav),
        media_type="audio/wav",
        headers={
            "Content-Disposition": 'inline; filename="out.wav"',
            "Cache-Control": "no-cache",
        },
    )


# ============================================================
# Frontend
# ============================================================

INDEX = FRONTEND_DIR / "index.html"


if INDEX.exists():

    app.mount(
        "/",
        StaticFiles(
            directory=str(FRONTEND_DIR),
            html=True,
        ),
        name="frontend",
    )

else:

    @app.get("/")
    def placeholder():
        return {
            "info": (
                "Frontend not built yet. "
                "Put index.html inside the frontend directory."
            ),
            "expected_index": str(INDEX),
        }


# ============================================================
# Server launcher
# ============================================================

def tts_run():
    import uvicorn
    uvicorn.run(
        app,
        host=ADMIN_HOST,
        port=ADMIN_PORT,
        reload=False,
        log_level="info",
    )


# ============================================================
# Main
# ============================================================
from engine import MODEL_DIR, REPO_DIR
if __name__ == "__main__":
    print("=" * 60)
    print("[engine] BASE_DIR  :", BASE_DIR)
    print("[engine] MODEL_DIR :", MODEL_DIR)
    print("[engine] REPO_DIR  :", REPO_DIR)
    print("[engine] model exists:", MODEL_DIR.exists())
    print("[engine] repo exists :", REPO_DIR.exists())
    print("=" * 60)
    tts_run()


import io
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from transformers import AutoTokenizer, T5ForConditionalGeneration

# ============================================================
# Paths
# ============================================================

# Expected project structure:
#
# chptc/
# ├── backend/
# │   ├── main.py
# │   ├── engine.py
# │   └── tts/
# │       └── model/
# │           └── pocket-tts/
# │
# └── voices/
#
# You can override these with environment variables.

BASE_DIR = Path(__file__).resolve().parent


MODEL_DIR = Path(
    os.environ.get(
        "PTTS_MODEL_DIR",
        str(
            BASE_DIR
            / "model"
            / "pocket-tts-farsi-v2"
        ),
    )
).resolve()


REPO_DIR = Path(
    os.environ.get(
        "PTTS_REPO_DIR",
        str(
            BASE_DIR
            / "tts"
            / "model"
            / "pocket-tts"
        ),
    )
).resolve()


CLI_MODULE = "training.farsi.synthesize"


# ============================================================
# G2P configuration
# ============================================================

G2P_MODEL = "mehdi-hf/Homo-GE2PE-Persian-HF"

TO_PHONEMES = str.maketrans(
    {
        "/": "a",
        "a": "A",
        "@": "?",
        "$": "S",
        "c": "C",
    }
)




# ============================================================
# Import local normalization
# ============================================================

if not MODEL_DIR.exists():
    raise FileNotFoundError(
        f"Pocket-TTS model directory does not exist:\n{MODEL_DIR}"
    )

# Allow:
# from normalize_fa import normalize_for_model
sys.path.insert(0, str(MODEL_DIR))

try:
    from normalize_fa import normalize_for_model
except ImportError as exc:
    raise ImportError(
        "Could not import 'normalize_for_model' from normalize_fa.py.\n"
        f"Expected normalize_fa.py inside:\n{MODEL_DIR}"
    ) from exc


# ============================================================
# Engine
# ============================================================

class FarsiTTSEngine:
    """
    Persian TTS engine.

    Pipeline:

        Persian text
             ↓
        normalize_for_model()
             ↓
        Persian G2P
             ↓
        phonemes
             ↓
        pocket-tts CLI
             ↓
        WAV
             ↓
        concatenate sentences
             ↓
        return WAV bytes
    """

    def __init__(self):
        print("=" * 60)
        print("[engine] Initializing Farsi TTS engine")
        print("=" * 60)

        print(f"[engine] model dir : {MODEL_DIR}")
        print(f"[engine] repo dir  : {REPO_DIR}")
        print(f"[engine] cli       : {CLI_MODULE}")
        print(f"[engine] python    : {sys.executable}")

        # ----------------------------------------------------
        # Validate directories
        # ----------------------------------------------------

        if not MODEL_DIR.exists():
            raise FileNotFoundError(
                f"Model directory not found:\n{MODEL_DIR}"
            )

        if not MODEL_DIR.is_dir():
            raise NotADirectoryError(
                f"MODEL_DIR is not a directory:\n{MODEL_DIR}"
            )

        if not REPO_DIR.exists():
            raise FileNotFoundError(
                f"Repository directory not found:\n{REPO_DIR}"
            )

        if not REPO_DIR.is_dir():
            raise NotADirectoryError(
                f"REPO_DIR is not a directory:\n{REPO_DIR}"
            )

        # ----------------------------------------------------
        # Locate YAML configuration
        # ----------------------------------------------------

        candidates = sorted(
            list(MODEL_DIR.glob("*.yaml"))
            + list(MODEL_DIR.glob("*.yml"))
        )

        if not candidates:
            raise FileNotFoundError(
                "No YAML configuration file was found in:\n"
                f"{MODEL_DIR}"
            )

        self.config_path = candidates[0]

        print(
            f"[engine] config    : "
            f"{self.config_path.name}"
        )

        # ----------------------------------------------------
        # Device
        # ----------------------------------------------------

        self.device = (
            "cuda"
            if torch.cuda.is_available()
            else "cpu"
        )

        print(f"[engine] G2P device: {self.device}")

        # ----------------------------------------------------
        # Load G2P tokenizer
        # ----------------------------------------------------

        print("[engine] loading G2P tokenizer...")

        self.g2p_tok = AutoTokenizer.from_pretrained(
            G2P_MODEL
        )

        # ----------------------------------------------------
        # Load G2P model
        # ----------------------------------------------------

        print("[engine] loading G2P model...")

        self.g2p_model = (
            T5ForConditionalGeneration
            .from_pretrained(G2P_MODEL)
            .to(self.device)
            .eval()
        )

        print("[engine] G2P ready")

        # Prevent two simultaneous syntheses from fighting
        # over temporary files / the CLI model.
        self._synthesis_lock = threading.Lock()

        print("[engine] Engine ready")
        print("=" * 60)

    # ========================================================
    # Text → phonemes
    # ========================================================

    def phonemise(self, text: str) -> str:
        """
        Convert Persian text into the phoneme representation
        expected by pocket-tts.
        """

        if not text or not text.strip():
            return ""

        # ----------------------------------------------------
        # Normalize Persian text
        # ----------------------------------------------------

        text = normalize_for_model(text)

        # Remove question marks because the G2P model does
        # not need them for phoneme generation.
        text = (
            text
            .replace("؟", "")
            .replace("?", "")
            .strip()
        )

        if not text:
            return ""

        # ----------------------------------------------------
        # Tokenize
        # ----------------------------------------------------

        enc = self.g2p_tok(
            [text],
            add_special_tokens=False,
            return_tensors="pt",
        )

        # Move tensors to same device as G2P model.
        enc = {
            key: value.to(self.device)
            for key, value in enc.items()
        }

        # ----------------------------------------------------
        # Generate phonemes
        # ----------------------------------------------------

        with torch.inference_mode():

            out = self.g2p_model.generate(
                **enc,
                num_beams=5,
                max_length=512,
                early_stopping=True,
            )

        # ----------------------------------------------------
        # Decode
        # ----------------------------------------------------

        raw = self.g2p_tok.batch_decode(
            out,
            skip_special_tokens=True,
        )[0].strip()

        if not raw:
            return ""

        # ----------------------------------------------------
        # Convert G2P symbols to pocket-tts symbols
        # ----------------------------------------------------

        # Remove ezafe marker "1".
        phonemes = (
            raw
            .translate(TO_PHONEMES)
            .replace("1", "")
            .strip()
        )

        return phonemes

    # ========================================================
    # One sentence → WAV
    # ========================================================

    def _synth_sentence(
        self,
        phonemes: str,
        voice_path: Path,
        out_path: Path,
    ):
        """
        Run the pocket-tts synthesis CLI for one sentence.
        """

        if not phonemes.strip():
            raise ValueError(
                "Cannot synthesize empty phonemes."
            )

        voice_path = Path(voice_path).resolve()
        out_path = Path(out_path).resolve()

        if not voice_path.exists():
            raise FileNotFoundError(
                f"Voice file not found:\n{voice_path}"
            )

        # ----------------------------------------------------
        # Use the same Python interpreter running FastAPI.
        # ----------------------------------------------------

        cmd = [
            sys.executable,
            "-m",
            CLI_MODULE,

            "--config",
            str(self.config_path),

            "--voice",
            str(voice_path),

            "--no-normalize-text",

            "--text",
            phonemes,

            "--out",
            str(out_path),
        ]

        print(
            "[engine] running CLI:",
            " ".join(cmd[:4]),
            "..."
        )

        try:
            result = subprocess.run(
                cmd,
                cwd=str(REPO_DIR),

                capture_output=True,
                text=True,

                timeout=180,

                # Make sure the child process sees the
                # same environment as FastAPI.
                env=os.environ.copy(),
            )

        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "Pocket-TTS synthesis timed out after 180 seconds."
            ) from exc

        except OSError as exc:
            raise RuntimeError(
                f"Could not start Pocket-TTS process: {exc}"
            ) from exc

        # ----------------------------------------------------
        # CLI failed
        # ----------------------------------------------------

        if result.returncode != 0:

            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()

            details = stderr[-2000:]

            if not details:
                details = stdout[-2000:]

            raise RuntimeError(
                "Pocket-TTS CLI failed "
                f"(exit code {result.returncode}).\n"
                f"{details}"
            )

        # ----------------------------------------------------
        # Verify output
        # ----------------------------------------------------

        if not out_path.exists():
            raise RuntimeError(
                "Pocket-TTS finished successfully, "
                "but did not create the WAV file:\n"
                f"{out_path}"
            )

        if out_path.stat().st_size == 0:
            raise RuntimeError(
                f"Generated WAV file is empty:\n{out_path}"
            )

    # ========================================================
    # Read generated WAV safely
    # ========================================================

    @staticmethod
    def _read_wav_with_retry(
        path: Path,
        retries: int = 20,
        delay: float = 0.1,
    ):
        """
        Windows sometimes keeps a newly created file locked
        briefly after a subprocess exits.

        Retry reading it.
        """

        last_error = None

        for _ in range(retries):

            try:
                wav, sr = sf.read(path)

                return wav, sr

            except Exception as exc:
                last_error = exc
                time.sleep(delay)

        raise RuntimeError(
            f"Could not read generated WAV:\n"
            f"{path}\n"
            f"Last error: {last_error}"
        )

    # ========================================================
    # Sentence splitting
    # ========================================================

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """
        Split Persian/English paragraphs into sentences.
        """

        # Normalize whitespace first.
        text = re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

        if not text:
            return []

        # Persian:
        #   .
        #   !
        #   ؟
        #
        # English:
        #   .
        #   !
        #   ?
        #
        # Keep punctuation attached to the sentence.
        sentences = re.split(
            r"(?<=[.!؟?])\s+",
            text,
        )

        return [
            sentence.strip()
            for sentence in sentences
            if sentence.strip()
        ]

    # ========================================================
    # Top-level synthesis
    # ========================================================

    def synthesize(
        self,
        text: str,
        voice_path: Path,
    ) -> bytes:
        """
        Synthesize an entire Persian paragraph.

        Returns:
            WAV bytes
        """

        if not text or not text.strip():
            raise ValueError(
                "Empty input."
            )

        voice_path = Path(
            voice_path
        ).resolve()

        if not voice_path.exists():
            raise FileNotFoundError(
                f"Voice file not found:\n{voice_path}"
            )

        if not voice_path.is_file():
            raise ValueError(
                f"Voice path is not a file:\n{voice_path}"
            )

        sentences = self._split_sentences(text)

        if not sentences:
            raise ValueError(
                "No sentences found in input."
            )

        print(
            f"[engine] synthesizing "
            f"{len(sentences)} sentence(s)"
        )

        # ----------------------------------------------------
        # Only one synthesis at a time.
        #
        # This is important because multiple browser requests
        # could otherwise start multiple CLI processes.
        # ----------------------------------------------------

        with self._synthesis_lock:

            chunks = []

            sr_final = None

            # ------------------------------------------------
            # Process each sentence
            # ------------------------------------------------

            for sentence_index, sentence in enumerate(
                sentences
            ):

                print(
                    f"[engine] sentence "
                    f"{sentence_index + 1}/"
                    f"{len(sentences)}"
                )

                # --------------------------------------------
                # G2P
                # --------------------------------------------

                phonemes = self.phonemise(
                    sentence
                )

                if not phonemes:
                    print(
                        f"[engine] no phonemes for "
                        f"sentence {sentence_index}"
                    )
                    continue

                # --------------------------------------------
                # Estimate expected duration
                # --------------------------------------------

                phoneme_tokens = len(
                    phonemes.split()
                )

                expected_sec = (
                    phoneme_tokens / 3.0
                    + 2.0
                )

                sentence_done = False

                # --------------------------------------------
                # Retry synthesis up to 3 times
                # --------------------------------------------

                for attempt in range(3):

                    try:

                        with tempfile.TemporaryDirectory(
                            prefix="ptts_"
                        ) as td:

                            tmp_wav = (
                                Path(td)
                                / (
                                    f"out_"
                                    f"{sentence_index}_"
                                    f"{attempt}.wav"
                                )
                            )

                            # -------------------------------
                            # CLI synthesis
                            # -------------------------------

                            self._synth_sentence(
                                phonemes,
                                voice_path,
                                tmp_wav,
                            )

                            # -------------------------------
                            # Read WAV
                            # -------------------------------

                            wav, sr = (
                                self._read_wav_with_retry(
                                    tmp_wav
                                )
                            )

                            # -------------------------------
                            # Stereo → mono
                            # -------------------------------

                            if wav.ndim > 1:
                                wav = wav.mean(
                                    axis=1
                                )

                            wav = np.asarray(
                                wav,
                                dtype=np.float32,
                            )

                            # -------------------------------
                            # Validate sample rate
                            # -------------------------------

                            if sr is None or sr <= 0:
                                raise RuntimeError(
                                    f"Invalid sample rate: {sr}"
                                )

                            # --------------------------------
                            # Validate duration
                            # --------------------------------

                            duration = (
                                len(wav) / sr
                            )

                            if (
                                duration
                                > expected_sec * 1.6
                            ):
                                print(
                                    "[engine] runaway "
                                    f"sentence "
                                    f"{sentence_index}, "
                                    f"attempt "
                                    f"{attempt + 1}"
                                )

                                continue

                            # --------------------------------
                            # Prevent accidental huge output
                            # --------------------------------

                            if duration > 120:
                                print(
                                    "[engine] generated "
                                    "audio is longer than "
                                    "120 seconds; retrying"
                                )
                                continue

                            # --------------------------------
                            # Sample-rate consistency
                            # --------------------------------

                            if sr_final is None:

                                sr_final = sr

                            elif sr != sr_final:

                                raise RuntimeError(
                                    "Sample-rate mismatch: "
                                    f"expected {sr_final}, "
                                    f"got {sr}"
                                )

                            # --------------------------------
                            # Save
                            # --------------------------------

                            chunks.append(wav)

                            sentence_done = True

                            print(
                                f"[engine] sentence "
                                f"{sentence_index + 1} "
                                f"done "
                                f"({duration:.2f}s)"
                            )

                            break

                    except Exception as exc:

                        print(
                            f"[engine] error "
                            f"(sentence "
                            f"{sentence_index + 1}, "
                            f"attempt "
                            f"{attempt + 1}): "
                            f"{exc}"
                        )

                        time.sleep(0.2)

                # --------------------------------------------
                # Sentence failed
                # --------------------------------------------

                if not sentence_done:

                    print(
                        f"[engine] giving up on "
                        f"sentence "
                        f"{sentence_index + 1}"
                    )

            # ------------------------------------------------
            # No audio
            # ------------------------------------------------

            if not chunks:
                raise RuntimeError(
                    "No audio was produced."
                )

            # ------------------------------------------------
            # Pause between sentences
            # ------------------------------------------------

            pause = np.zeros(
                int(
                    0.25 * sr_final
                ),
                dtype=np.float32,
            )

            output_chunks = []

            for index, chunk in enumerate(chunks):

                output_chunks.append(
                    chunk
                )

                if index < len(chunks) - 1:

                    output_chunks.append(
                        pause
                    )

            # ------------------------------------------------
            # Concatenate
            # ------------------------------------------------

            audio = np.concatenate(
                output_chunks
            )

            # ------------------------------------------------
            # Convert to WAV bytes
            # ------------------------------------------------

            buffer = io.BytesIO()

            sf.write(
                buffer,
                audio,
                sr_final,
                format="WAV",
            )

            buffer.seek(0)

            result = buffer.getvalue()

            if not result:
                raise RuntimeError(
                    "Generated WAV bytes are empty."
                )

            print(
                f"[engine] final audio: "
                f"{len(result) / 1024:.1f} KB"
            )

            return result


# ============================================================
# Singleton engine
# ============================================================

_engine: FarsiTTSEngine | None = None

_engine_lock = threading.Lock()


def get_engine() -> FarsiTTSEngine:
    """
    Return the singleton TTS engine.

    Thread-safe so two requests cannot initialize the large
    G2P model simultaneously.
    """

    global _engine

    if _engine is None:

        with _engine_lock:

            if _engine is None:

                print(
                    "[engine] creating singleton engine..."
                )

                _engine = FarsiTTSEngine()

    return _engine


print("=" * 60)
print("[engine] BASE_DIR  :", BASE_DIR)
print("[engine] MODEL_DIR :", MODEL_DIR)
print("[engine] REPO_DIR  :", REPO_DIR)
print("[engine] model exists:", MODEL_DIR.exists())
print("[engine] repo exists :", REPO_DIR.exists())
print("=" * 60)

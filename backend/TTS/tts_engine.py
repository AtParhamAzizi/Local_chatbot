from __future__ import annotations

import importlib.util
import re
import sys
import threading
from pathlib import Path
from typing import Any

import numpy as np
from settings import load_settings

if __package__:
    from .text_router import URL_RE, split_text
else:
    from text_router import URL_RE, split_text


class ModelLoadError(RuntimeError):
    """An expected model/dependency setup error."""


class SpeechEngine:
    """Loads both speech models once and reuses them for every request."""

    SAMPLE_RATE = 24_000
    URL_WORD_REPLACEMENTS = (
        ("https://", "https colon slash slash "),
        ("http://", "http colon slash slash "),
        ("://", " colon slash slash "),
        ("/", " slash "),
        (".", " dot "),
        ("-", " dash "),
        ("_", " underscore "),
        ("?", " question mark "),
        ("&", " and "),
        ("=", " equals "),
        ("#", " hash "),
    )

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir).resolve()
        self.settings = load_settings(scope="tts", tts_base=self.base_dir)
        self.persian_dir = self.base_dir / "Persian_model"
        self.english_dir = self.base_dir / "English_model"
        self.runtime_dir = self.base_dir / ".runtime"
        self.runtime_dir.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.english = None
        self.persian = None
        self.persian_voice_state = None
        self.g2p_tokenizer = None
        self.g2p_model = None
        self.normalize_persian = None
        self._load_models()

    def _load_models(self) -> None:
        try:
            self._load_english()
            self._load_persian()
        except ModelLoadError:
            raise
        except Exception as error:
            raise ModelLoadError(self._friendly_error(error)) from error

    def _load_english(self) -> None:
        try:
            from kokoro_onnx import Kokoro
        except ImportError as error:
            raise ModelLoadError("Install the English dependencies with `pip install -r requirements.txt`.") from error

        model_name = self.settings["ENGLISH_MODEL_FILE"]
        model_path = self.english_dir / "onnx" / model_name
        if not model_path.exists():
            raise ModelLoadError(f"English model file was not found: {model_path}")

        voices_path = self._build_voice_archive()
        self.english = Kokoro(str(model_path), str(voices_path))

    def _build_voice_archive(self) -> Path:
        archive_path = self.runtime_dir / "english-voices.bin"
        voice_files = sorted((self.english_dir / "voices").glob("*.bin"))
        if not voice_files:
            raise ModelLoadError(f"No English voices were found in {self.english_dir / 'voices'}")

        voices: dict[str, np.ndarray] = {}
        for voice_file in voice_files:
            raw = np.fromfile(voice_file, dtype=np.float32)
            if not raw.size or raw.size % 256 or not np.isfinite(raw).all():
                raise ModelLoadError(f"Invalid voice file: {voice_file}")
            voices[voice_file.stem] = raw.reshape(-1, 1, 256)

        with archive_path.open("wb") as handle:
            np.savez(handle, **voices)
        return archive_path

    def _load_persian(self) -> None:
        try:
            import torch
            for package in ("torchvision", "torchaudio", "torchcodec", "librosa"):
                sys.modules.setdefault(package, None)
            from transformers import AutoTokenizer, T5ForConditionalGeneration
            from pocket_tts import TTSModel
        except ImportError as error:
            raise ModelLoadError(
                "Install Persian dependencies with `pip install -r requirements.txt`."
            ) from error

        torch.set_num_threads(self.settings["TTS_CPU_THREADS"])
        config_path = self._local_persian_config()
        self.persian = TTSModel.load_model(config=config_path, quantize=True)
        self.persian.eval()

        normalization_path = self.persian_dir / "normalize_fa.py"
        if not normalization_path.is_file():
            raise ModelLoadError(f"Persian normalization file was not found: {normalization_path}")
        spec = importlib.util.spec_from_file_location("tts_normalize_fa", normalization_path)
        normalization = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(normalization)
        self.normalize_persian = normalization.normalize_for_model

        voice_prompt = self.settings["PERSIAN_VOICE_PROMPT"]
        self.persian_voice_state = self.persian.get_state_for_audio_prompt(voice_prompt)

        self.g2p_tokenizer = AutoTokenizer.from_pretrained(self.settings["PERSIAN_G2P_REPO"])
        self.g2p_model = T5ForConditionalGeneration.from_pretrained(self.settings["PERSIAN_G2P_REPO"]).to("cpu").eval()

    def _local_persian_config(self) -> Path:
        source = self.persian_dir / "model.yaml"
        if not source.exists():
            raise ModelLoadError(f"Persian model config was not found: {source}")

        target = self.runtime_dir / "persian-local.yaml"
        weights_path = (self.persian_dir / "model.safetensors").resolve().as_posix()
        tokenizer_path = (self.persian_dir / "tokenizer_ph.model").resolve().as_posix()
        for path in (weights_path, tokenizer_path):
            if not Path(path).is_file():
                raise ModelLoadError(f"Persian model file was not found: {path}")
        config = source.read_text(encoding="utf-8")
        config = re.sub(r"^weights_path:.*$", f'weights_path: "{weights_path}"', config, flags=re.MULTILINE)
        config = re.sub(
            r"^    tokenizer_path:.*$",
            f'    tokenizer_path: "{tokenizer_path}"',
            config,
            flags=re.MULTILINE,
        )
        target.write_text(config, encoding="utf-8")
        return target

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.english is not None and self.persian is not None and self.g2p_model is not None,
            "device": "cpu",
            "english_model": self.settings["ENGLISH_MODEL_FILE"],
            "english_voice_count": len(self.english.get_voices()) if self.english is not None else 0,
            "persian_model": "Persian_model/model.safetensors",
        }

    def synthesize(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int, int]:
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 5_000:
            raise ValueError("Text must contain between 1 and 5,000 characters.")
        if not isinstance(voice, str):
            raise ValueError("Voice must be a string.")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not np.isfinite(speed) or not 0.5 <= speed <= 2.0:
            raise ValueError("Speed must be between 0.5 and 2.0.")
        segments = split_text(text)
        if not segments:
            raise ValueError("No speakable text was found.")

        parts: list[np.ndarray] = []
        with self.lock:
            for segment in segments:
                if segment.language == "persian":
                    audio = self._synthesize_persian(segment.text)
                else:
                    audio = self._synthesize_english(segment.text, voice, speed)
                if not np.isfinite(audio).all():
                    raise RuntimeError("The models returned invalid audio.")
                if audio.size:
                    if parts:
                        parts.append(np.zeros(int(self.SAMPLE_RATE * 0.08), dtype=np.float32))
                    parts.append(audio)

        if not parts:
            raise ValueError("The models returned no audio.")
        merged = np.concatenate(parts).astype(np.float32)
        peak = float(np.max(np.abs(merged)))
        if peak > 0.98:
            merged *= 0.98 / peak
        return merged, self.SAMPLE_RATE, len(segments)

    def _synthesize_english(self, text: str, voice: str, speed: float) -> np.ndarray:
        if self.english is None:
            raise ModelLoadError("The English model is not loaded.")
        available = self.english.get_voices()
        if voice not in available:
            raise ValueError(f"Unknown English voice: {voice}")
        spoken_text = URL_RE.sub(lambda match: self._url_to_speech(match.group()), text)
        language = "en-gb" if voice.startswith(("bf_", "bm_")) else "en-us"
        audio, sample_rate = self.english.create(spoken_text, voice, speed=speed, lang=language)
        if sample_rate != self.SAMPLE_RATE:
            raise ModelLoadError(f"Unexpected English sample rate: {sample_rate}")
        return np.asarray(audio, dtype=np.float32).reshape(-1)

    def _synthesize_persian(self, text: str) -> np.ndarray:
        if self.persian is None or self.g2p_model is None or self.persian_voice_state is None:
            raise ModelLoadError("The Persian model is not loaded.")

        import torch
        pieces: list[np.ndarray] = []
        sentences = [part.strip() for part in re.split(r"(?<=[.!?؟])\s+", text) if part.strip()]
        # Keep long chatbot sentences within the phoneme decoder's token budget.
        bounded = []
        for sentence in sentences:
            while len(sentence) > 160:
                end = sentence.rfind(' ', 0, 161)
                if end < 40:
                    end = 160
                bounded.append(sentence[:end].strip())
                sentence = sentence[end:].strip()
            if sentence:
                bounded.append(sentence)
        for sentence in bounded:
            normalized = self.normalize_persian(sentence).replace("؟", "").replace("?", "").strip()
            if not normalized:
                continue
            encoded = self.g2p_tokenizer([normalized], add_special_tokens=False, return_tensors="pt")
            with torch.no_grad():
                generated = self.g2p_model.generate(**encoded, num_beams=5, max_length=512, early_stopping=True)
            raw = self.g2p_tokenizer.batch_decode(generated, skip_special_tokens=True)[0].strip()
            phonemes = raw.translate(str.maketrans({"/": "a", "a": "A", "@": "?", "$": "S", "c": "C"}))
            if not phonemes:
                continue
            if pieces:
                pieces.append(np.zeros(int(self.SAMPLE_RATE * 0.25), dtype=np.float32))
            for chunk in self._phoneme_chunks(phonemes):
                with torch.no_grad():
                    generated_audio = self.persian.generate_audio(
                        self.persian_voice_state,
                        chunk,
                        frames_after_eos=2,
                        copy_state=True,
                    )
                audio = generated_audio.detach().cpu().numpy().reshape(-1).astype(np.float32)
                pieces.append(audio)
        return np.concatenate(pieces) if pieces else np.empty(0, dtype=np.float32)

    def _phoneme_chunks(self, phonemes: str) -> list[str]:
        tokenizer = self.persian.flow_lm.conditioner.tokenizer.sp
        chunks = []
        words = []
        for word in phonemes.split():
            candidate = " ".join([*words, word]).replace("1", "")
            if words and len(tokenizer.encode(candidate)) > 18 and not words[-1].endswith("1"):
                chunks.append(" ".join(words).replace("1", ""))
                words = []
            words.append(word)
        if words:
            chunks.append(" ".join(words).replace("1", ""))
        return chunks

    def _url_to_speech(self, value: str) -> str:
        spoken = re.sub(r"^https?://", lambda match: match.group().lower(), value, flags=re.IGNORECASE)
        for source, replacement in self.URL_WORD_REPLACEMENTS:
            spoken = spoken.replace(source, replacement)
        return spoken

    @staticmethod
    def _friendly_error(error: Exception) -> str:
        message = str(error).strip()
        if "espeak" in message.lower():
            return f"English phonemizer setup failed: {message}"
        if message:
            return message
        return error.__class__.__name__

from __future__ import annotations

import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

import numpy as np

from text_router import TextSegment, split_text


class ModelLoadError(RuntimeError):
    """An expected model/dependency setup error."""


class SpeechEngine:
    """Loads both speech models once and reuses them for every request."""

    SAMPLE_RATE = 24_000
    G2P_REPO = os.getenv("PERSIAN_G2P_REPO", "mehdi-hf/Homo-GE2PE-Persian-HF")
    URL_WORD_REPLACEMENTS = (
        ("https://", "https slash slash "),
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
        self.base_dir = base_dir
        self.persian_dir = base_dir / "Persian_model"
        self.english_dir = base_dir / "English_model"
        self.runtime_dir = base_dir / ".runtime"
        self.runtime_dir.mkdir(exist_ok=True)
        if str(self.persian_dir) not in sys.path:
            sys.path.insert(0, str(self.persian_dir))
        self.lock = threading.RLock()
        self.english = None
        self.persian = None
        self.persian_voice_state = None
        self.g2p_tokenizer = None
        self.g2p_model = None
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

        model_name = os.getenv("ENGLISH_MODEL_FILE", "model_quantized.onnx")
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

        newest_source = max(path.stat().st_mtime_ns for path in voice_files)
        if archive_path.exists() and archive_path.stat().st_mtime_ns >= newest_source:
            return archive_path

        voices: dict[str, np.ndarray] = {}
        for voice_file in voice_files:
            raw = np.fromfile(voice_file, dtype=np.float32)
            if raw.size % 256:
                raise ModelLoadError(f"Invalid voice file: {voice_file}")
            voices[voice_file.stem] = raw.reshape(-1, 1, 256)

        with archive_path.open("wb") as handle:
            np.savez(handle, **voices)
        return archive_path

    def _load_persian(self) -> None:
        try:
            import torch
            from transformers import AutoTokenizer, T5ForConditionalGeneration
            from pocket_tts import TTSModel
        except ImportError as error:
            raise ModelLoadError(
                "Install Persian dependencies with `pip install -r requirements.txt`."
            ) from error

        torch.set_num_threads(int(os.getenv("TTS_CPU_THREADS", "1")))
        config_path = self._local_persian_config()
        self.persian = TTSModel.load_model(config=config_path, quantize=True)
        self.persian.eval()

        voice_prompt = os.getenv(
            "PERSIAN_VOICE_PROMPT",
            "hf://mehdi-hf/pocket-tts-farsi-v2/samples/prompt_hello.wav",
        )
        self.persian_voice_state = self.persian.get_state_for_audio_prompt(voice_prompt)

        self.g2p_tokenizer = AutoTokenizer.from_pretrained(self.G2P_REPO)
        self.g2p_model = T5ForConditionalGeneration.from_pretrained(self.G2P_REPO).to("cpu").eval()

    def _local_persian_config(self) -> Path:
        source = self.persian_dir / "model.yaml"
        if not source.exists():
            raise ModelLoadError(f"Persian model config was not found: {source}")

        target = self.runtime_dir / "persian-local.yaml"
        weights_path = (self.persian_dir / "model.safetensors").resolve().as_posix()
        tokenizer_path = (self.persian_dir / "tokenizer_ph.model").resolve().as_posix()
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
            "english_model": os.getenv("ENGLISH_MODEL_FILE", "model_quantized.onnx"),
            "english_voice_count": len(self.english.get_voices()) if self.english is not None else 0,
            "persian_model": "Persian_model/model.safetensors",
        }

    def synthesize(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int, int]:
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
        selected_voice = voice if voice in available else os.getenv("ENGLISH_VOICE", "af_heart")
        spoken_text = self._url_to_speech(text) if "http://" in text.lower() or "https://" in text.lower() else text
        audio, sample_rate = self.english.create(spoken_text, selected_voice, speed=speed, lang="en-us")
        if sample_rate != self.SAMPLE_RATE:
            raise ModelLoadError(f"Unexpected English sample rate: {sample_rate}")
        return np.asarray(audio, dtype=np.float32).reshape(-1)

    def _synthesize_persian(self, text: str) -> np.ndarray:
        if self.persian is None or self.g2p_model is None or self.persian_voice_state is None:
            raise ModelLoadError("The Persian model is not loaded.")

        import torch
        from normalize_fa import normalize_for_model

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
            normalized = normalize_for_model(sentence)
            encoded = self.g2p_tokenizer([normalized], add_special_tokens=False, return_tensors="pt")
            with torch.no_grad():
                generated = self.g2p_model.generate(**encoded, num_beams=5, max_length=512, early_stopping=True)
            raw = self.g2p_tokenizer.batch_decode(generated, skip_special_tokens=True)[0].strip()
            phonemes = raw.translate(str.maketrans({"/": "a", "a": "A", "@": "?", "$": "S", "c": "C"}))
            phonemes = phonemes.replace("1", "").strip()
            if not phonemes:
                continue
            with torch.no_grad():
                generated_audio = self.persian.generate_audio(
                    self.persian_voice_state,
                    phonemes,
                    frames_after_eos=2,
                    copy_state=True,
                )
            audio = generated_audio.detach().cpu().numpy().reshape(-1).astype(np.float32)
            if pieces:
                pieces.append(np.zeros(int(self.SAMPLE_RATE * 0.25), dtype=np.float32))
            pieces.append(audio)
        return np.concatenate(pieces) if pieces else np.empty(0, dtype=np.float32)

    def _url_to_speech(self, value: str) -> str:
        spoken = value
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

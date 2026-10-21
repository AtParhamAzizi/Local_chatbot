from collections import deque
from dataclasses import dataclass
import math

import numpy as np


SAMPLE_RATE = 16000
FRAME_SAMPLES = SAMPLE_RATE // 10
MAX_PACKET_BYTES = SAMPLE_RATE


@dataclass
class TranscriptionJob:
    segment_id: int
    audio: np.ndarray
    is_final: bool


class AudioStream:
    def __init__(self, voice_threshold=0.008):
        if not math.isfinite(voice_threshold) or voice_threshold <= 0:
            raise ValueError("STT_VOICE_THRESHOLD must be a positive finite number.")
        self.voice_threshold = voice_threshold
        self.partial_interval = SAMPLE_RATE
        self.silence_limit = int(SAMPLE_RATE * 0.7)
        self.segment_limit = SAMPLE_RATE * 12
        self.prefix = deque(maxlen=3)
        self.pending = deque()
        self.remainder = np.empty(0, dtype=np.float32)
        self.frames = []
        self.sample_count = 0
        self.silence_count = 0
        self.last_partial = 0
        self.segment_id = 0
        self.finished = False

    @property
    def has_pending(self):
        return bool(self.pending)

    def feed(self, packet):
        if self.finished:
            raise ValueError("The audio stream is already finished.")
        if not packet or len(packet) % 2 or len(packet) > MAX_PACKET_BYTES:
            raise ValueError("Expected at most 500 ms of mono, 16 kHz, little-endian PCM16.")
        samples = np.frombuffer(packet, dtype="<i2").astype(np.float32) / 32768.0
        samples = np.concatenate((self.remainder, samples))
        frame_end = samples.size - samples.size % FRAME_SAMPLES
        for offset in range(0, frame_end, FRAME_SAMPLES):
            self._feed_frame(samples[offset:offset + FRAME_SAMPLES].copy())
        self.remainder = samples[frame_end:].copy()

    def _feed_frame(self, frame):
        voiced = float(np.sqrt(np.mean(frame * frame))) >= self.voice_threshold
        if not self.frames:
            if not voiced:
                self.prefix.append(frame)
                return
            self.frames.extend(self.prefix)
            self.sample_count = sum(prefix.size for prefix in self.prefix)
            self.prefix.clear()

        self.frames.append(frame)
        self.sample_count += frame.size
        self.silence_count = 0 if voiced else self.silence_count + frame.size
        if self.silence_count >= self.silence_limit or self.sample_count >= self.segment_limit:
            self._finalize()

    def _finalize(self):
        if not self.frames:
            return
        if len(self.pending) >= 3:
            raise RuntimeError("Transcription cannot keep up. Try STT_MODEL=tiny or a faster device.")
        self.pending.append(TranscriptionJob(
            self.segment_id, np.concatenate(self.frames), True
        ))
        self.segment_id += 1
        self.frames = []
        self.sample_count = 0
        self.silence_count = 0
        self.last_partial = 0

    def next_job(self):
        if self.pending:
            return self.pending.popleft()
        if self.frames and self.sample_count - self.last_partial >= self.partial_interval:
            self.last_partial = self.sample_count
            return TranscriptionJob(self.segment_id, np.concatenate(self.frames), False)
        return None

    def finish(self):
        if self.finished:
            return
        if self.remainder.size:
            self._feed_frame(self.remainder)
            self.remainder = np.empty(0, dtype=np.float32)
        self._finalize()
        self.finished = True

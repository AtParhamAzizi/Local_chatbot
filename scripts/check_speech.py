"""Generate a short real TTS reply and check its WAV without starting chat/DB."""

import argparse
import json
import math
import os
from pathlib import Path
import struct
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='Use only cached model files')
    parser.add_argument('--text', default='سلام، پاسخ شما آماده است. Hello.')
    options = parser.parse_args()
    if options.offline:
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
    from speech import SpeechService
    service = SpeechService()
    try:
        audio = service.synthesize(options.text)
        if audio[:4] != b'RIFF' or audio[8:12] != b'WAVE':
            raise ValueError('Expected a WAV response.')
        offset, sample_rate, samples = 12, None, None
        while offset + 8 <= len(audio):
            chunk, size = struct.unpack_from('<4sI', audio, offset)
            payload = audio[offset + 8:offset + 8 + size]
            if chunk == b'fmt ':
                encoding, channels, sample_rate = struct.unpack_from('<HHI', payload)
                if encoding != 3 or channels != 1:
                    raise ValueError('Expected mono float32 model audio.')
            elif chunk == b'data':
                samples = [item[0] for item in struct.iter_unpack('<f', payload)]
            offset += 8 + size + size % 2
        if sample_rate != 24000 or not samples or not all(map(math.isfinite, samples)):
            raise ValueError('Invalid model waveform.')
        if max(map(abs, samples)) < 0.00001:
            raise ValueError('Model returned silent audio.')
        print(json.dumps({'status': 'ready', 'sample_rate': sample_rate,
                          'duration_seconds': round(len(samples) / sample_rate, 2), 'wav_bytes': len(audio)}))
        return 0
    finally:
        service.close()


if __name__ == '__main__':
    raise SystemExit(main())

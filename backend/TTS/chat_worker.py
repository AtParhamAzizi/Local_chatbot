"""Private stdin/stdout adapter; no HTTP server or database access."""

import base64
import io
import json
import os
from pathlib import Path
import sys


def main():
    protocol = sys.stdout
    # Model libraries may print progress; stdout belongs to the JSON protocol.
    sys.stdout = sys.stderr
    try:
        from tts_engine import SpeechEngine
        from scipy.io import wavfile
        engine = SpeechEngine(Path(__file__).resolve().parent)
    except Exception as error:
        print('TTS initialization failed (' + type(error).__name__ + ').', file=sys.stderr, flush=True)
        protocol.write(json.dumps({'ok': False}) + '\n')
        protocol.flush()
        return 1
    protocol.write(json.dumps({'ok': True}) + '\n')
    protocol.flush()
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            value = payload['text']
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 5000:
                raise ValueError('Invalid speech text.')
            audio, sample_rate, _ = engine.synthesize(value, os.getenv('ENGLISH_VOICE', 'af_heart'), 1.0)
            output = io.BytesIO()
            wavfile.write(output, sample_rate, audio)
            result = {'ok': True, 'wav': base64.b64encode(output.getvalue()).decode('ascii')}
        except Exception as error:
            print('TTS synthesis failed (' + type(error).__name__ + ').', file=sys.stderr, flush=True)
            result = {'ok': False}
        protocol.write(json.dumps(result) + '\n')
        protocol.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

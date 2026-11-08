"""Lazy local TTS worker, isolated from the chatbot's model dependencies."""

import atexit
import base64
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from flask import current_app, jsonify, send_file

from abuse_controls import limit_requests, rate_limited_response
from public_errors import public_error
from request_validation import json_object, text


ROOT = Path(__file__).resolve().parents[1]
MAX_SPEECH_CHARS = 5000


class SpeechUnavailable(RuntimeError):
    pass


class SpeechBusy(RuntimeError):
    pass


class SpeechService:
    def __init__(self):
        self.lock = threading.Lock()
        self.slot = threading.BoundedSemaphore(1)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tts')
        self.future = None
        self.process = None
        atexit.register(self.close)

    def _receive(self):
        line = self.process.stdout.readline()
        if not line:
            raise SpeechUnavailable('TTS worker stopped.')
        result = json.loads(line)
        if not result.get('ok'):
            raise SpeechUnavailable('TTS worker could not complete the request.')
        return result

    def _load(self):
        executable = os.getenv('TTS_PYTHON')
        if not executable:
            isolated = ROOT / '.venv-tts' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            executable = str(isolated) if isolated.is_file() else sys.executable
        self.process = subprocess.Popen(
            [executable, '-B', '-u', str(ROOT / 'backend/TTS/chat_worker.py')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding='utf-8', bufsize=1, cwd=ROOT,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        self._receive()

    def warmup(self):
        with self.lock:
            if self.future is None:
                self.future = self.executor.submit(self._load)

    def status(self):
        if self.future is None or not self.future.done():
            return 'loading'
        if self.future.exception() is not None or self.process.poll() is not None:
            return 'unavailable'
        return 'ready'

    def _render(self, value):
        self.future.result()
        self.process.stdin.write(json.dumps({'text': value}, ensure_ascii=False) + '\n')
        self.process.stdin.flush()
        return base64.b64decode(self._receive()['wav'], validate=True)

    def synthesize(self, value):
        if not self.slot.acquire(blocking=False):
            raise SpeechBusy()
        try:
            self.warmup()
            # Model loading is queued first; keep the slot until actual work ends,
            # even if the HTTP caller times out or disconnects.
            future = self.executor.submit(self._render, value)
        except BaseException:
            self.slot.release()
            raise
        future.add_done_callback(lambda completed: self.slot.release())
        try:
            return future.result(timeout=240)
        except (TimeoutError, OSError, ValueError, KeyError) as error:
            raise SpeechUnavailable() from error

    def close(self):
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        self.executor.shutdown(wait=False, cancel_futures=True)


speech_service = SpeechService()


def configure_speech(app, *, service=None):
    if 'speech_service' in app.extensions:
        return
    app.extensions['speech_service'] = speech_service if service is None else service

    @app.get('/tts/health')
    @limit_requests('tts_health')
    def tts_health():
        service = current_app.extensions['speech_service']
        service.warmup()
        status = service.status()
        if status != 'ready':
            return jsonify(status=status, **public_error(503, 'tts_' + status)), 503
        return jsonify(status='ready')

    @app.post('/tts/synthesize')
    @limit_requests('tts')
    def tts_synthesize():
        payload = json_object({'text': text(MAX_SPEECH_CHARS, 1)}, required=('text',))
        try:
            audio = current_app.extensions['speech_service'].synthesize(payload['text'])
        except SpeechBusy:
            return rate_limited_response(5)
        except Exception as error:
            logging.getLogger(__name__).error('Local TTS unavailable (%s)', type(error).__name__)
            return jsonify(public_error(503, 'tts_unavailable')), 503
        response = send_file(io.BytesIO(audio), mimetype='audio/wav', download_name='reply.wav')
        response.headers['Cache-Control'] = 'no-store'
        return response

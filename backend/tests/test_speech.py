"""Local speech HTTP contracts and worker admission without loading real models."""

import importlib
import io
import sys
import unittest
from concurrent.futures import Future, TimeoutError
from pathlib import Path
from unittest.mock import Mock, patch

from browser_support import BrowserApps


class SpeechEndpointTests(unittest.TestCase):
    def setUp(self):
        self.apps = BrowserApps('http://localhost:5000', 'http://localhost:8000')
        self.apps.__enter__()
        self.addCleanup(self.apps.__exit__, None, None, None)
        self.client = self.apps.ui_app.test_client()

    def test_wave_reply_is_private_and_contains_only_the_requested_text(self):
        response = self.client.post('/tts/synthesize', json={'text': 'سلام hello'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'audio/wav')
        self.assertEqual(response.data[:4], b'RIFF')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.apps.speech_requests, ['سلام hello'])
        self.assertEqual(self.apps.history.rows, {})

    def test_invalid_input_never_calls_speech_models(self):
        for payload in ({}, {'text': ''}, {'text': ' '}, {'text': []}, {'text': 'x' * 5001},
                        {'text': 'hello', 'voice': 'unexpected'}):
            with self.subTest(payload_type=type(payload)):
                self.assertEqual(self.client.post('/tts/synthesize', json=payload).status_code, 400)
        self.assertEqual(self.apps.speech_requests, [])

    def test_failures_are_public_and_leave_chat_available(self):
        self.apps.speech_unavailable = True
        response = self.client.post('/tts/synthesize', json={'text': 'hello'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json['code'], 'tts_unavailable')
        self.assertNotIn('Injected', response.get_data(as_text=True))
        self.assertEqual(self.client.get('/tts/health').status_code, 503)
        self.assertEqual(self.client.post('/chat', json={'message': 'typed message'}).status_code, 200)

    def test_concurrent_speech_is_rejected_before_model_work(self):
        limiter = self.apps.ui_app.extensions['abuse_controls']['tts']
        self.assertEqual(limiter.acquire('127.0.0.1'), 0)
        try:
            response = self.client.post('/tts/synthesize', json={'text': 'hello'})
        finally:
            limiter.release('127.0.0.1')
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response.headers)
        self.assertEqual(self.apps.speech_requests, [])


class SpeechWorkerTests(unittest.TestCase):
    def setUp(self):
        backend = str(Path(__file__).resolve().parents[1])
        self.path = patch.object(sys, 'path', [backend, *sys.path])
        self.path.start()
        self.addCleanup(self.path.stop)
        self.module = importlib.import_module('speech')
        self.service = self.module.SpeechService()
        self.addCleanup(self.service.close)

    def test_models_are_not_loaded_until_warmup_and_worker_is_reused(self):
        process = Mock(stdout=io.StringIO('{"ok": true}\n{"ok": true, "wav": "UklGRg=="}\n'),
                       stdin=io.StringIO())
        process.poll.return_value = 0
        with patch.object(self.module.subprocess, 'Popen', return_value=process) as start:
            self.assertIsNone(self.service.future)
            self.assertEqual(self.service.synthesize('سلام'), b'RIFF')
            self.service.warmup()
            self.service.future.result(timeout=2)
        start.assert_called_once()
        self.assertIn('سلام', process.stdin.getvalue())
        self.assertEqual(start.call_args.kwargs['env']['PYTHONIOENCODING'], 'utf-8')

    def test_global_slot_rejects_parallel_clients(self):
        self.service.slot.acquire()
        try:
            with self.assertRaises(self.module.SpeechBusy):
                self.service.synthesize('hello')
        finally:
            self.service.slot.release()
        self.assertIsNone(self.service.future)

    def test_failed_loading_releases_the_admission_slot(self):
        with patch.object(self.service, '_load', side_effect=self.module.SpeechUnavailable()):
            with self.assertRaises(self.module.SpeechUnavailable):
                self.service.synthesize('hello')
        self.assertTrue(self.service.slot.acquire(blocking=False))
        self.service.slot.release()

    def test_http_timeout_does_not_admit_another_client_while_worker_is_busy(self):
        ready, pending = Future(), Future()
        ready.set_result(None)
        with patch.object(self.service.executor, 'submit', side_effect=[ready, pending]), \
                patch.object(pending, 'result', side_effect=TimeoutError()):
            with self.assertRaises(self.module.SpeechUnavailable):
                self.service.synthesize('hello')
        self.assertFalse(self.service.slot.acquire(blocking=False))
        pending.set_result(b'RIFF')
        self.assertTrue(self.service.slot.acquire(blocking=False))
        self.service.slot.release()


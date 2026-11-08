"""TTS text routing, model adapters, and standalone HTTP regressions."""

import importlib
import logging
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]


def tts_module(name):
    with patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]):
        return importlib.import_module('TTS.' + name)


class TextRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.router = tts_module('text_router')

    def test_mixed_text_keeps_language_order_and_joiners(self):
        segments = self.router.split_text('سلام meeting می\u200cروم.')
        self.assertEqual([(part.language, part.text) for part in segments],
                         [('persian', 'سلام'), ('english', 'meeting'), ('persian', 'می\u200cروم.')])

    def test_url_keeps_query_exclamation_and_fragment(self):
        url = 'HTTPS://example.com/a?q=hello!&id=42#part'
        segments = self.router.split_text('ببین ' + url + '، لطفاً')
        self.assertEqual([(part.language, part.text) for part in segments],
                         [('persian', 'ببین'), ('english', url + '،'), ('persian', 'لطفاً')])

    def test_persian_punctuation_does_not_change_english_language(self):
        segments = self.router.split_text('Hello، world!')
        self.assertEqual([(part.language, part.text) for part in segments],
                         [('english', 'Hello، world!')])

    def test_leading_punctuation_and_numbers_follow_first_word(self):
        segments = self.router.split_text('(123 سلام)')
        self.assertEqual([(part.language, part.text) for part in segments],
                         [('persian', '(123 سلام)')])

    def test_accented_words_are_preserved(self):
        self.assertEqual(self.router.split_text('café déjà vu')[0].text, 'café déjà vu')

    def test_accented_words_adjacent_to_persian_are_routed_separately(self):
        self.assertEqual([(part.language, part.text) for part in self.router.split_text('caféسلام')],
                         [('english', 'café'), ('persian', 'سلام')])

    def test_persian_numbers_use_persian_model(self):
        self.assertEqual(self.router.split_text('۱۲۳')[0].language, 'persian')

    def test_punctuation_only_is_not_speakable(self):
        for text in ('', '  ', '...؟!', '🙂'):
            with self.subTest(text=text):
                self.assertEqual(self.router.split_text(text), [])


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.module = tts_module('tts_engine')
        except ModuleNotFoundError as error:
            if error.name == 'numpy':
                raise unittest.SkipTest('TTS engine tests require the optional NumPy dependency') from error
            raise
        cls.np = cls.module.np

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        with patch.object(self.module.SpeechEngine, '_load_models'):
            self.engine = self.module.SpeechEngine(self.base)
        self.engine.english = Mock()
        self.engine.english.get_voices.return_value = ['af_heart', 'bf_emma']
        self.engine.english.create.return_value = (self.np.array([0.2, -0.2]), 24000)

    def test_url_expansion_does_not_change_surrounding_prose(self):
        self.engine._synthesize_english('Visit HTTPS://example.com/a?q=x&n=1. Well-known.', 'af_heart', 1.0)
        text = self.engine.english.create.call_args.args[0]
        self.assertIn('https colon slash slash example dot com slash a question mark q equals x and n equals 1', text)
        self.assertTrue(text.endswith(' Well-known.'))

    def test_unavailable_voice_is_a_validation_error(self):
        with self.assertRaises(ValueError):
            self.engine._synthesize_english('Hello', 'missing', 1.0)
        self.engine.english.create.assert_not_called()

    def test_british_voices_use_british_phonemizer(self):
        self.engine._synthesize_english('Hello', 'bf_emma', 1.0)
        self.assertEqual(self.engine.english.create.call_args.kwargs['lang'], 'en-gb')

    def test_voice_archive_reflects_removed_and_changed_sources(self):
        voices = self.base / 'English_model' / 'voices'
        voices.mkdir(parents=True)
        first, second = voices / 'af_heart.bin', voices / 'bf_emma.bin'
        self.np.ones(256, dtype=self.np.float32).tofile(first)
        self.np.zeros(256, dtype=self.np.float32).tofile(second)
        self.engine._build_voice_archive()
        second.unlink()
        self.np.full(256, 2, dtype=self.np.float32).tofile(first)
        with self.np.load(self.engine._build_voice_archive()) as archive:
            self.assertEqual(archive.files, ['af_heart'])
            self.assertEqual(archive['af_heart'].shape, (1, 1, 256))
            self.assertTrue((archive['af_heart'] == 2).all())

    def test_empty_voice_file_is_rejected(self):
        voices = self.base / 'English_model' / 'voices'
        voices.mkdir(parents=True)
        (voices / 'af_heart.bin').touch()
        with self.assertRaises(self.module.ModelLoadError):
            self.engine._build_voice_archive()

    def test_nonfinite_model_audio_is_rejected(self):
        self.engine.english.create.return_value = (self.np.array([float('nan')]), 24000)
        with self.assertRaisesRegex(RuntimeError, 'invalid audio'):
            self.engine.synthesize('Hello', 'af_heart', 1.0)

    def test_invalid_speed_is_rejected_before_model_work(self):
        for speed in (True, None, [], '1.0', float('nan'), float('inf'), 0.1, 3):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                self.engine.synthesize('Hello', 'af_heart', speed)
        self.engine.english.create.assert_not_called()

    def test_persian_question_marks_are_removed_before_g2p(self):
        self.engine.persian = Mock()
        self.engine.persian_voice_state = {}
        self.engine.normalize_persian = lambda text: text
        self.engine.g2p_tokenizer = Mock(return_value={'input_ids': 'tokens'})
        self.engine.g2p_tokenizer.batch_decode.return_value = ['sal/m h/le1 Som/']
        self.engine.g2p_model = Mock()
        self.engine.persian.flow_lm.conditioner.tokenizer.sp.encode.side_effect = str.split
        self.engine.persian.generate_audio.return_value.detach.return_value.cpu.return_value.numpy.return_value = self.np.ones(32)
        with patch.dict(sys.modules, {'torch': SimpleNamespace(no_grad=nullcontext)}):
            self.engine._synthesize_persian('سلام؟ حال شما?')
        self.assertEqual(self.engine.g2p_tokenizer.call_args_list[0].args[0], ['سلام'])
        self.assertEqual(self.engine.g2p_tokenizer.call_args_list[1].args[0], ['حال شما'])
        self.assertEqual(self.engine.persian.generate_audio.call_args.args[1], 'sAlam hale Soma')

    def test_long_phonemes_are_chunked_without_breaking_ezafe(self):
        self.engine.persian = Mock()
        self.engine.persian.flow_lm.conditioner.tokenizer.sp.encode.side_effect = str.split
        chunks = self.engine._phoneme_chunks(' '.join(['word'] * 17 + ['bound1', 'noun'] + ['tail'] * 10))
        self.assertEqual(len(chunks), 2)
        self.assertIn('bound noun', chunks[0])
        self.assertNotIn('1', ' '.join(chunks))
        self.assertEqual(' '.join(chunks).split(), ['word'] * 17 + ['bound', 'noun'] + ['tail'] * 10)

    def test_persian_loader_uses_local_prompt_without_sys_path_changes(self):
        persian = self.base / 'Persian_model'
        persian.mkdir()
        (persian / 'normalize_fa.py').write_text('def normalize_for_model(text):\n    return text\n', encoding='utf-8')
        loader = Mock()
        modules = {'torch': Mock(), 'transformers': Mock(), 'pocket_tts': SimpleNamespace(TTSModel=loader)}
        with patch.dict(sys.modules, modules), patch.dict('os.environ', {}, clear=True), \
                patch.object(self.engine, '_local_persian_config', return_value=persian / 'model.yaml'):
            before = sys.path.copy()
            self.engine._load_persian()
        self.assertEqual(before, sys.path)
        loader.load_model.return_value.get_state_for_audio_prompt.assert_called_once_with(str(self.base / 'voices' / 'prompt_hello.wav'))
        self.assertEqual(self.engine.normalize_persian('سلام'), 'سلام')


class StandaloneAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class ModelLoadError(RuntimeError):
            pass

        engine_module = SimpleNamespace(ModelLoadError=ModelLoadError, SpeechEngine=Mock())
        with patch.dict(sys.modules, {'TTS.tts_engine': engine_module}):
            cls.module = tts_module('app')

    def setUp(self):
        self.engine = Mock()
        self.engine.status.return_value = {'ready': True}
        self.factory = Mock(return_value=self.engine)
        self.app = self.module.create_app(self.factory)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_home_and_invalid_requests_do_not_load_models(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        for payload in ([], ['text'], 'hello', 123, None, {}, {'text': None}, {'text': []}, {'text': 123},
                        {'text': ' '}, {'text': 'x' * 5001}, {'text': 'Hello', 'voice': []},
                        {'text': 'Hello', 'speed': []}, {'text': 'Hello', 'speed': True},
                        {'text': 'Hello', 'speed': 'NaN'}, {'text': 'Hello', 'speed': 'inf'},
                        {'text': 'Hello', 'speed': 0.1}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post('/api/synthesize', json=payload).status_code, 400)
        self.factory.assert_not_called()

    def test_status_reuses_one_engine(self):
        self.assertEqual(self.client.get('/api/status').json, {'ready': True})
        self.assertEqual(self.client.get('/api/status').json, {'ready': True})
        self.factory.assert_called_once()

    def test_model_loading_failure_returns_503_and_keeps_ui_available(self):
        self.factory.side_effect = self.module.ModelLoadError('Missing local model')
        with patch.object(logging.Logger, 'exception'):
            self.assertEqual(self.client.get('/api/status').status_code, 503)
        self.assertEqual(self.client.post('/api/synthesize', json={'text': 'Hello'}).status_code, 503)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.factory.assert_called_once()

    def test_engine_input_errors_return_400(self):
        self.engine.synthesize.side_effect = ValueError('No speakable text was found.')
        response = self.client.post('/api/synthesize', json={'text': '...؟'})
        self.assertEqual(response.status_code, 400)

    def test_success_returns_private_wave_and_segment_count(self):
        self.engine.synthesize.return_value = ([0.2], 24000, 2)
        wavfile = SimpleNamespace(write=lambda output, sample_rate, audio: output.write(b'RIFFwave'))
        with patch.dict(sys.modules, {'scipy.io': SimpleNamespace(wavfile=wavfile)}):
            response = self.client.post('/api/synthesize', json={'text': ' سلام Hello ', 'speed': '1.2'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b'RIFFwave')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['X-TTS-Segments'], '2')
        self.engine.synthesize.assert_called_once_with('سلام Hello', 'af_heart', 1.2)

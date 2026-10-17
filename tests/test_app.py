import queue
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _stub_missing(name, **attrs):
    # Allow importing app.py on machines without GUI/audio packages (e.g. Linux CI).
    try:
        __import__(name)
    except ImportError:
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module


_stub_missing('soundcard')
_stub_missing('openai', OpenAI=object)
try:
    import tkinter  # noqa: F401
except ImportError:
    tk_stub = types.ModuleType('tkinter')
    for sub in ('ttk', 'messagebox', 'filedialog'):
        setattr(tk_stub, sub, types.ModuleType('tkinter.' + sub))
        sys.modules['tkinter.' + sub] = getattr(tk_stub, sub)
    sys.modules['tkinter'] = tk_stub

import app  # noqa: E402


def make_ui():
    ui = app.TranslatorUI.__new__(app.TranslatorUI)
    ui.events = queue.Queue()
    ui.translation_pool = app.concurrent.futures.ThreadPoolExecutor(max_workers=1)
    ui.bedrock = mock.Mock()
    return ui


def bedrock_reply(text):
    return {'output': {'message': {'content': [{'text': text}]}}}


class MakeAwsSessionTest(unittest.TestCase):
    def test_uses_given_credentials(self):
        session = app.make_aws_session('ap-northeast-1', ('AKIDLOGIN', 'secret', 'token'))
        creds = session.get_credentials()
        self.assertEqual((creds.access_key, creds.secret_key, creds.token), ('AKIDLOGIN', 'secret', 'token'))
        self.assertEqual(session.region_name, 'ap-northeast-1')

    def test_without_token(self):
        creds = app.make_aws_session('ap-northeast-1', ('AKIDMANUAL', 'secret', None)).get_credentials()
        self.assertEqual(creds.access_key, 'AKIDMANUAL')
        self.assertIsNone(creds.token)

    def test_none_falls_back_to_default_chain(self):
        with mock.patch.object(app.boto3, 'Session') as session:
            app.make_aws_session('us-east-1', None)
        session.assert_called_once_with(region_name='us-east-1')


class TranslationStopTest(unittest.TestCase):
    def tearDown(self):
        self.ui.translation_pool.shutdown(wait=True, cancel_futures=True)

    def test_schedule_skips_stopped_session(self):
        self.ui = make_ui()
        stop = threading.Event()
        stop.set()
        with mock.patch.object(self.ui.translation_pool, 'submit') as submit:
            self.ui.schedule_translation('相手', 'hello', stop)
        submit.assert_not_called()

    def test_translate_does_not_call_bedrock_after_stop(self):
        self.ui = make_ui()
        stop = threading.Event()
        stop.set()
        self.ui.translate_with_bedrock('相手', 'hello', stop, self.ui.bedrock)
        self.ui.bedrock.converse.assert_not_called()
        self.assertTrue(self.ui.events.empty())

    def test_translate_uses_bound_client(self):
        self.ui = make_ui()
        bound = mock.Mock()
        bound.converse.return_value = bedrock_reply('en\nこんにちは')
        self.ui.translate_with_bedrock('相手', 'hello', threading.Event(), bound)
        bound.converse.assert_called_once()
        self.ui.bedrock.converse.assert_not_called()
        self.assertEqual(self.ui.events.get_nowait(), ('caption', '相手', 'hello', 'こんにちは', 'en'))

    def test_scheduled_task_keeps_its_session_client(self):
        self.ui = make_ui()
        old_client = self.ui.bedrock
        old_client.converse.return_value = bedrock_reply('en\nこんにちは')
        gate = threading.Event()
        self.ui.translation_pool.submit(gate.wait)
        self.ui.schedule_translation('相手', 'hello', threading.Event())
        self.ui.bedrock = mock.Mock()  # a new session replaces the client
        gate.set()
        self.ui.translation_pool.shutdown(wait=True)
        old_client.converse.assert_called_once()
        self.ui.bedrock.converse.assert_not_called()

    def test_cancel_drops_queued_translations(self):
        self.ui = make_ui()
        gate = threading.Event()
        old_pool = self.ui.translation_pool
        old_pool.submit(gate.wait)  # occupy the only worker
        self.ui.schedule_translation('相手', 'hello', threading.Event())
        self.ui.cancel_translations()
        gate.set()
        old_pool.shutdown(wait=True)
        self.ui.bedrock.converse.assert_not_called()
        self.assertIsNot(self.ui.translation_pool, old_pool)
        self.assertTrue(self.ui.events.empty())

    def test_schedule_after_pool_shutdown_is_ignored(self):
        self.ui = make_ui()
        self.ui.translation_pool.shutdown(wait=True)
        self.ui.schedule_translation('相手', 'hello', threading.Event())
        self.ui.translation_pool = app.concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def test_error_suppressed_after_stop(self):
        self.ui = make_ui()
        stop = threading.Event()
        client = mock.Mock()

        def stop_then_fail(**_):
            stop.set()
            raise RuntimeError('boom')

        client.converse.side_effect = stop_then_fail
        self.ui.translate_with_bedrock('相手', 'hello', stop, client)
        self.assertTrue(self.ui.events.empty())


if __name__ == '__main__':
    unittest.main()

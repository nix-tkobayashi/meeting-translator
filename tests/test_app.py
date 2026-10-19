import queue
import sys
import threading
import time
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
    for sub in ('ttk', 'messagebox', 'filedialog', 'font'):
        setattr(tk_stub, sub, types.ModuleType('tkinter.' + sub))
        sys.modules['tkinter.' + sub] = getattr(tk_stub, sub)
    sys.modules['tkinter'] = tk_stub

import app  # noqa: E402


def make_ui():
    ui = app.TranslatorUI.__new__(app.TranslatorUI)
    ui.events = queue.Queue()
    ui.translation_pools = {'相手': app.concurrent.futures.ThreadPoolExecutor(max_workers=1)}
    ui.pools_lock = threading.Lock()
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
        for pool in self.ui.translation_pools.values():
            pool.shutdown(wait=True, cancel_futures=True)

    def test_schedule_skips_stopped_session(self):
        self.ui = make_ui()
        stop = threading.Event()
        stop.set()
        with mock.patch.object(self.ui.translation_pools['相手'], 'submit') as submit:
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
        self.ui.translation_pools['相手'].submit(gate.wait)
        self.ui.schedule_translation('相手', 'hello', threading.Event())
        self.ui.bedrock = mock.Mock()  # a new session replaces the client
        gate.set()
        self.ui.translation_pools['相手'].shutdown(wait=True)
        old_client.converse.assert_called_once()
        self.ui.bedrock.converse.assert_not_called()

    def test_cancel_drops_queued_translations(self):
        self.ui = make_ui()
        gate = threading.Event()
        old_pool = self.ui.translation_pools['相手']
        old_pool.submit(gate.wait)  # occupy the only worker
        self.ui.schedule_translation('相手', 'hello', threading.Event())
        self.ui.cancel_translations()
        gate.set()
        old_pool.shutdown(wait=True)
        self.ui.bedrock.converse.assert_not_called()
        self.assertEqual(self.ui.translation_pools, {})
        self.assertTrue(self.ui.events.empty())

    def test_schedule_after_cancel_starts_fresh_pool(self):
        self.ui = make_ui()
        self.ui.bedrock.converse.return_value = bedrock_reply('en\nこんにちは')
        self.ui.cancel_translations()
        self.ui.schedule_translation('相手', 'hello', threading.Event())
        self.ui.translation_pools['相手'].shutdown(wait=True)
        self.ui.bedrock.converse.assert_called_once()

    def test_captions_keep_order_per_speaker(self):
        self.ui = make_ui()
        self.ui.translation_pools = {}
        delays = {'first.': 0.2, 'second.': 0.0}

        def converse(messages, **_):
            text = messages[0]['content'][0]['text']
            time.sleep(delays.get(text, 0))
            return bedrock_reply('en\n' + text.upper())

        self.ui.bedrock.converse.side_effect = converse
        stop = threading.Event()
        for text in ('first.', 'second.'):
            self.ui.schedule_translation('相手', text, stop)
        self.ui.schedule_translation('自分', 'mine.', stop)
        self.assertEqual(set(self.ui.translation_pools), {'相手', '自分'})
        for pool in self.ui.translation_pools.values():
            pool.shutdown(wait=True)
        captions = [e[2] for e in list(self.ui.events.queue) if e[1] == '相手']
        self.assertEqual(captions, ['first.', 'second.'])

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


class ParseAwsEnvTest(unittest.TestCase):
    EXPECTED = {'AWS_ACCESS_KEY_ID': 'AKIAEXAMPLE', 'AWS_SECRET_ACCESS_KEY': 'se/cr+et', 'AWS_SESSION_TOKEN': 'tok=='}

    def test_bash_export(self):
        text = 'export AWS_ACCESS_KEY_ID="AKIAEXAMPLE"\nexport AWS_SECRET_ACCESS_KEY="se/cr+et"\nexport AWS_SESSION_TOKEN="tok=="\n'
        self.assertEqual(app.parse_aws_env(text), self.EXPECTED)

    def test_cmd_and_powershell_and_crlf(self):
        text = 'set AWS_ACCESS_KEY_ID=AKIAEXAMPLE\r\nSET "AWS_SECRET_ACCESS_KEY=se/cr+et"\r\n$Env:AWS_SESSION_TOKEN="tok==";\r\n'
        self.assertEqual(app.parse_aws_env(text), self.EXPECTED)

    def test_ignores_other_lines_and_empty_values(self):
        text = "# comment\nexport AWS_REGION=us-east-1\nexport AWS_ACCESS_KEY_ID='AKIAEXAMPLE'\nexport AWS_SESSION_TOKEN=\"\""
        self.assertEqual(app.parse_aws_env(text), {'AWS_ACCESS_KEY_ID': 'AKIAEXAMPLE'})

    def test_plain_value(self):
        self.assertEqual(app.parse_aws_env('AKIAEXAMPLE'), {})


class PickFontTest(unittest.TestCase):
    def test_prefers_yu_gothic_ui(self):
        self.assertEqual(app.pick_font(['Segoe UI', 'Meiryo UI', 'Yu Gothic UI']), 'Yu Gothic UI')

    def test_japanese_family_name(self):
        self.assertEqual(app.pick_font(['メイリオ UI']), 'メイリオ UI')

    def test_fallback(self):
        self.assertEqual(app.pick_font(['Segoe UI']), 'TkDefaultFont')


def _real_tk():
    try:
        root = app.tk.Tk()
    except Exception:
        return None
    return root


@unittest.skipUnless(hasattr(app.tk, 'Tk'), 'tkinter not available')
class UiTest(unittest.TestCase):
    def setUp(self):
        self.root = _real_tk()
        if self.root is None:
            self.skipTest('no display for Tk')
        self.addCleanup(self.root.destroy)
        sc_stub = mock.Mock(all_speakers=mock.Mock(return_value=[]), all_microphones=mock.Mock(return_value=[]))
        patches = [mock.patch.object(app, 'sc', sc_stub),
                   mock.patch.object(app.updater, 'check_for_update', return_value=None)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.ui = app.TranslatorUI(self.root)
        self.addCleanup(self.ui.cancel_translations)
        self.root.update()

    def test_settings_toggle(self):
        self.assertTrue(self.ui.cfg.winfo_manager())
        self.ui.toggle_settings()
        self.assertFalse(self.ui.cfg.winfo_manager())
        self.assertIn('表示', self.ui.settings_button.cget('text'))
        self.ui.toggle_settings()
        self.root.update()
        self.assertTrue(self.ui.cfg.winfo_manager())
        # Settings come back directly below the button bar, above the captions.
        self.assertLess(self.ui.cfg.winfo_y(), self.ui.output.winfo_rooty() - self.root.winfo_rooty())

    def test_paste_fills_all_fields(self):
        self.root.clipboard_clear()
        self.root.clipboard_append('export AWS_ACCESS_KEY_ID="AKIAEXAMPLE"\nexport AWS_SECRET_ACCESS_KEY="secret"\n')
        self.ui.session_token.set('stale')
        self.ui.credential_entries[0].event_generate('<<Paste>>')
        self.assertEqual(self.ui.access_key.get(), 'AKIAEXAMPLE')
        self.assertEqual(self.ui.secret_key.get(), 'secret')
        self.assertEqual(self.ui.session_token.get(), '')

    def test_single_value_paste_is_normal(self):
        self.root.clipboard_clear()
        self.root.clipboard_append('AKIAONLY')
        entry = self.ui.credential_entries[0]
        entry.focus_force()
        entry.event_generate('<<Paste>>')
        self.assertEqual(self.ui.access_key.get(), 'AKIAONLY')
        self.assertEqual(self.ui.secret_key.get(), '')

    def test_japanese_font_applied(self):
        if self.ui.font == 'TkDefaultFont':
            self.skipTest('no Japanese UI font installed')
        family = app.tkfont.nametofont('TkDefaultFont').cget('family')
        self.assertEqual(family, self.ui.font)


if __name__ == '__main__':
    unittest.main()

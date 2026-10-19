import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import updater  # noqa: E402

SETUP_URL = 'https://github.com/nix-tkobayashi/meeting-translator/releases/download/v0.2.0/MeetingTranslator-Setup-0.2.0.exe'


def release(tag='v0.2.0', name='MeetingTranslator-Setup-0.2.0.exe', digest='sha256:ABC', url=SETUP_URL):
    return {'tag_name': tag, 'assets': [
        {'name': 'MeetingTranslator-Windows.zip', 'digest': 'sha256:zip', 'browser_download_url': url},
        {'name': name, 'digest': digest, 'browser_download_url': url},
    ]}


class FakeResponse(io.BytesIO):
    def __init__(self, data, url=SETUP_URL):
        super().__init__(data)
        self._url = url

    def geturl(self):
        return self._url


class ParseVersionTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(updater.parse_version('v1.2.10'), (1, 2, 10))
        self.assertEqual(updater.parse_version('0.1.0'), (0, 1, 0))
        self.assertIsNone(updater.parse_version('0.0.0-dev'))
        self.assertIsNone(updater.parse_version(None))


class FindUpdateTest(unittest.TestCase):
    def test_newer_release(self):
        update = updater.find_update('0.1.0', release())
        self.assertEqual(update, {'version': '0.2.0', 'name': 'MeetingTranslator-Setup-0.2.0.exe',
                                  'url': SETUP_URL, 'sha256': 'abc'})

    def test_numeric_comparison(self):
        self.assertIsNotNone(updater.find_update('0.9.0', release(tag='v0.10.0')))

    def test_same_or_older(self):
        self.assertIsNone(updater.find_update('0.2.0', release()))
        self.assertIsNone(updater.find_update('0.3.0', release()))

    def test_dev_build_never_updates(self):
        self.assertIsNone(updater.find_update('0.0.0-dev', release()))

    def test_requires_sha256_digest(self):
        self.assertIsNone(updater.find_update('0.1.0', release(digest=None)))

    def test_rejects_foreign_url(self):
        with self.assertRaises(ValueError):
            updater.find_update('0.1.0', release(url='https://evil.example.com/setup.exe'))
        with self.assertRaises(ValueError):
            updater.find_update('0.1.0', release(url='http://github.com/x.exe'))

    def test_check_skips_network_for_dev(self):
        with mock.patch.object(updater.urllib.request, 'urlopen') as urlopen:
            self.assertIsNone(updater.check_for_update('0.0.0-dev'))
        urlopen.assert_not_called()

    def test_check_reads_latest_release(self):
        body = FakeResponse(json.dumps(release()).encode())
        with mock.patch.object(updater.urllib.request, 'urlopen', return_value=body) as urlopen:
            self.assertEqual(updater.check_for_update('0.1.0')['version'], '0.2.0')
        self.assertEqual(urlopen.call_args[0][0].full_url, updater.LATEST_URL)


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def update_for(self, data):
        return {'version': '0.2.0', 'name': 'MeetingTranslator-Setup-0.2.0.exe', 'url': SETUP_URL,
                'sha256': hashlib.sha256(data).hexdigest()}

    def test_verified_download(self):
        data = b'installer-bytes' * 1000
        with mock.patch.object(updater.urllib.request, 'urlopen', return_value=FakeResponse(data)):
            path = updater.download(self.update_for(data), self.tmp.name)
        self.assertEqual(path.read_bytes(), data)

    def test_hash_mismatch_deletes_file(self):
        update = self.update_for(b'expected')
        with mock.patch.object(updater.urllib.request, 'urlopen', return_value=FakeResponse(b'tampered')):
            with self.assertRaises(ValueError):
                updater.download(update, self.tmp.name)
        self.assertFalse((Path(self.tmp.name) / update['name']).exists())

    def test_redirect_to_foreign_host_rejected(self):
        data = b'x'
        response = FakeResponse(data, url='https://evil.example.com/setup.exe')
        with mock.patch.object(updater.urllib.request, 'urlopen', return_value=response):
            with self.assertRaises(ValueError):
                updater.download(self.update_for(data), self.tmp.name)


class LaunchTest(unittest.TestCase):
    def test_waits_for_app_then_runs_silent_installer(self):
        with mock.patch.object(updater.subprocess, 'Popen') as popen:
            updater.launch_installer(Path("C:/Users/o'neil/setup.exe"), 4321)
        args = popen.call_args[0][0]
        self.assertEqual(args[0], 'powershell.exe')
        script = args[-1]
        self.assertLess(script.index('Wait-Process -Id 4321'), script.index('Start-Process'))
        self.assertIn("o''neil", script)  # single quote escaped for PowerShell
        self.assertIn("'/SILENT'", script)


if __name__ == '__main__':
    unittest.main()

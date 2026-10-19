"""In-app update from GitHub Releases: find a newer Setup EXE, verify its SHA-256, run it silently."""
import hashlib
import json
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

REPO = 'nix-tkobayashi/meeting-translator'
LATEST_URL = f'https://api.github.com/repos/{REPO}/releases/latest'
SETUP_PATTERN = re.compile(r'^MeetingTranslator-Setup-[0-9.]+\.exe$')
ALLOWED_HOSTS = {'github.com', 'api.github.com', 'objects.githubusercontent.com',
                 'release-assets.githubusercontent.com'}


def parse_version(text):
    """'v1.2.3' -> (1, 2, 3); None for dev builds or anything not plain numeric."""
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', (text or '').strip())
    return tuple(int(part) for part in match.groups()) if match else None


def _check_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError('想定外の更新URLです: ' + url)
    return url


def find_update(current_version, release):
    """Return {'version', 'url', 'sha256', 'name'} if `release` (GitHub API JSON) is newer, else None."""
    current = parse_version(current_version)
    latest = parse_version(release.get('tag_name'))
    if current is None or latest is None or latest <= current:
        return None
    for asset in release.get('assets', []):
        name = asset.get('name', '')
        digest = asset.get('digest') or ''
        if SETUP_PATTERN.match(name) and digest.startswith('sha256:'):
            return {'version': '.'.join(map(str, latest)), 'name': name,
                    'url': _check_url(asset['browser_download_url']), 'sha256': digest.split(':', 1)[1].lower()}
    return None


def check_for_update(current_version, timeout=10):
    if parse_version(current_version) is None:
        return None  # source/dev builds never self-update
    request = urllib.request.Request(LATEST_URL, headers={'Accept': 'application/vnd.github+json',
                                                          'User-Agent': 'MeetingTranslator'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return find_update(current_version, json.load(response))


def download(update, dest_dir=None, timeout=60):
    """Download the installer and verify SHA-256 against the release asset digest; return its path."""
    dest = Path(dest_dir or tempfile.mkdtemp(prefix='MeetingTranslator-update-')) / update['name']
    request = urllib.request.Request(_check_url(update['url']), headers={'User-Agent': 'MeetingTranslator'})
    sha = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=timeout) as response, open(dest, 'wb') as out:
        _check_url(response.geturl())  # redirect target must also be GitHub
        for chunk in iter(lambda: response.read(1 << 16), b''):
            sha.update(chunk)
            out.write(chunk)
    if sha.hexdigest() != update['sha256']:
        dest.unlink(missing_ok=True)
        raise ValueError('ダウンロードした更新ファイルのハッシュが一致しません。')
    return dest


INSTALLER_ARGS = ('/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS')


def launch_installer(path, wait_pid):
    """Start Setup once process `wait_pid` (this app) has exited, so no installed file is locked.
    The installer relaunches the app after a silent install (see installer/MeetingTranslator.iss)."""
    quoted = str(path).replace("'", "''")
    args = ','.join(f"'{a}'" for a in INSTALLER_ARGS)
    script = (f'Wait-Process -Id {int(wait_pid)} -ErrorAction SilentlyContinue; '
              f"Start-Process -FilePath '{quoted}' -ArgumentList {args}")
    subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', script],
                     close_fds=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

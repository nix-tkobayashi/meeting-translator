"""Cognito hosted browser login (authorization code + PKCE) -> temporary AWS credentials.
No AWS access keys or refresh tokens are persisted.
"""
import base64
import hashlib
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import boto3
from botocore import UNSIGNED
from botocore.config import Config

CALLBACK = 'http://localhost:8765/callback'


def load_public_config():
    # Only non-secret Cognito identifiers; distributed alongside executable.
    path = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent) / 'config.json'
    if not path.exists():
        raise ValueError('config.json がありません。READMEの手順でCognito設定を配置してください。')
    config = json.loads(path.read_text(encoding='utf-8'))
    required = ('region', 'user_pool_id', 'client_id', 'identity_pool_id', 'domain')
    if any(not config.get(k) for k in required):
        raise ValueError('config.json のCognito設定が不足しています。')
    return config


def browser_login(config, timeout=180):
    """Return temporary credentials; listener accepts only local callback and checks state."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    state = secrets.token_urlsafe(24)
    result = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed.query)
            if parsed.path != '/callback' or q.get('state', [''])[0] != state:
                self.send_error(400, 'Invalid callback/state')
                return
            result['code'] = q.get('code', [None])[0]
            result['error'] = q.get('error', [None])[0]
            payload = 'ログイン処理を受け付けました。アプリに戻ってください。'.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            done.set()

    server = HTTPServer(('127.0.0.1', 8765), Handler)
    server.timeout = 1
    domain = config['domain'].strip().rstrip('/')
    if not domain.startswith('https://'):
        raise ValueError('Cognito domainにはhttps:// URLが必要です。')
    params = urllib.parse.urlencode({'response_type': 'code', 'client_id': config['client_id'],
                                     'redirect_uri': CALLBACK, 'scope': 'openid email',
                                     'code_challenge_method': 'S256', 'code_challenge': challenge, 'state': state})
    url = domain + '/oauth2/authorize?' + params
    try:
        if not webbrowser.open(url):
            raise RuntimeError('ブラウザを開けませんでした。')
        import time
        end = time.monotonic() + timeout
        while not done.is_set() and time.monotonic() < end:
            server.handle_request()
        if not done.is_set():
            raise TimeoutError('ブラウザ認証がタイムアウトしました。')
        if not result.get('code'):
            raise RuntimeError('Cognito認証エラー: ' + str(result.get('error')))
    finally:
        server.server_close()

    data = urllib.parse.urlencode({'grant_type': 'authorization_code', 'client_id': config['client_id'],
                                   'code': result['code'], 'redirect_uri': CALLBACK,
                                   'code_verifier': verifier}).encode()
    request = urllib.request.Request(domain + '/oauth2/token', data=data,
                                     headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
    with urllib.request.urlopen(request, timeout=20) as response:
        token = json.load(response)['id_token']
    provider = f"cognito-idp.{config['region']}.amazonaws.com/{config['user_pool_id']}"
    client = boto3.client('cognito-identity', region_name=config['region'], config=Config(signature_version=UNSIGNED))
    logins = {provider: token}
    identity = client.get_id(IdentityPoolId=config['identity_pool_id'], Logins=logins)['IdentityId']
    credentials = client.get_credentials_for_identity(IdentityId=identity, Logins=logins)['Credentials']
    return (credentials['AccessKeyId'], credentials['SecretKey'], credentials['SessionToken'])

"""Bot-free, two-way meeting captions (Windows prototype)."""
import io
import asyncio
import concurrent.futures
import json
import os
import queue
import re
import threading
import time
import wave
from datetime import datetime
from auth import load_public_config, browser_login
from pathlib import Path

import numpy as np
import soundcard as sc
from openai import OpenAI
import boto3
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

RATE = 24000
CHUNK_SECONDS = 5
STREAM_FRAMES = 1600  # 100ms at 16 kHz
SILENCE_RMS = 0.0025


def to_wav(audio):
    mono = np.asarray(audio, dtype=np.float32)
    if mono.ndim == 2:
        mono = mono.mean(axis=1)
    pcm = (np.clip(mono, -1, 1) * 32767).astype('<i2')
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(pcm.tobytes())
    buf.seek(0)
    buf.name = 'audio.wav'
    return buf


def make_aws_session(region, credentials):
    # credentials: (access_key, secret_key, session_token) or None for the default profile chain.
    if credentials:
        access_key, secret_key, session_token = credentials
        return boto3.Session(region_name=region, aws_access_key_id=access_key,
                             aws_secret_access_key=secret_key, aws_session_token=session_token)
    return boto3.Session(region_name=region)


class TranslatorUI:
    def __init__(self, root):
        self.root = root
        self.root.title('Meeting Translator — Botなし双方向字幕')
        self.root.geometry('1000x820')
        self.root.minsize(700, 480)
        self.root.configure(bg='#101622')
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.active = False
        self.history = []
        self.speakers = []
        self.mics = []
        self.api_client = None
        self.translation_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self.aws_session = None
        self.bedrock = None
        self.runtime_region = None
        self.runtime_credentials = None
        self.login_credentials = None
        self.login_config = None
        self.mode_choice = tk.StringVar(value="個人モード")
        self.make_ui()
        self.refresh_devices()
        self.root.after(100, self.process_events)
        self.root.protocol('WM_DELETE_WINDOW', self.close)

    def make_ui(self):
        s = ttk.Style()
        s.theme_use('clam')
        s.configure('TCombobox', padding=5)
        top = tk.Frame(self.root, bg='#101622')
        top.pack(fill='x', padx=22, pady=(20, 6))
        tk.Label(top, text='Meeting Translator', bg='#101622', fg='#f8fafc', font=('Segoe UI', 22, 'bold')).pack(side='left')
        tk.Label(top, text='LOCAL CAPTURE · NO MEETING BOT', bg='#101622', fg='#7db3ff', font=('Segoe UI', 9, 'bold')).pack(side='right')
        cfg = tk.Frame(self.root, bg='#1a2435', padx=14, pady=12)
        cfg.pack(fill='x', padx=22, pady=12)
        tk.Label(cfg, text='相手の音声（スピーカー）', bg='#1a2435', fg='white').grid(row=0, column=0, sticky='w')
        tk.Label(cfg, text='自分の音声（マイク）', bg='#1a2435', fg='white').grid(row=0, column=1, sticky='w', padx=(12,0))
        self.speaker_choice = ttk.Combobox(cfg, state='readonly')
        self.speaker_choice.grid(row=1, column=0, sticky='ew', pady=6)
        self.mic_choice = ttk.Combobox(cfg, state='readonly')
        self.mic_choice.grid(row=1, column=1, sticky='ew', padx=(12, 0), pady=6)
        tk.Label(cfg, text='音声認識', bg='#1a2435', fg='white').grid(row=2, column=0, sticky='w', pady=(8,0))
        self.backend = ttk.Combobox(cfg, state='readonly', values=['Amazon Transcribe Streaming', 'OpenAI (5秒チャンク)'])
        self.backend.grid(row=3, column=0, sticky='ew', pady=(4, 0))
        self.backend.current(0)
        tk.Label(cfg, text='AWSリージョン', bg='#1a2435', fg='white').grid(row=2, column=1, sticky='w', padx=(12,0), pady=(8,0))
        self.region = tk.StringVar(value=os.getenv('AWS_REGION', 'ap-northeast-1'))
        ttk.Entry(cfg, textvariable=self.region).grid(row=3, column=1, sticky='ew', padx=(12,0), pady=(4,0))
        # Credentials entered here remain in process memory only; never written to a file.
        tk.Label(cfg, text='AWS認証情報（3項目を貼り付け / 空欄なら既存のAWSプロファイルを使用）',
                 bg='#1a2435', fg='#b9cce6').grid(row=4, column=0, columnspan=2, sticky='w', pady=(14, 4))
        self.access_key = tk.StringVar()
        self.secret_key = tk.StringVar()
        self.session_token = tk.StringVar()
        self.credential_entries = []
        credential_fields = [
            ('AWS_ACCESS_KEY_ID', self.access_key, False),
            ('AWS_SECRET_ACCESS_KEY', self.secret_key, True),
            ('AWS_SESSION_TOKEN（有効期限のある認証情報の場合）', self.session_token, True),
        ]
        for row_index, (label, variable, secret) in enumerate(credential_fields, start=5):
            tk.Label(cfg, text=label, bg='#1a2435', fg='white').grid(row=row_index, column=0, sticky='w', pady=(6, 0))
            entry = ttk.Entry(cfg, textvariable=variable, show='•' if secret else '')
            entry.grid(row=row_index, column=1, sticky='ew', padx=(12, 0), pady=(6, 0))
            self.credential_entries.append(entry)
        tk.Label(cfg, text='利用モード', bg='#1a2435', fg='white').grid(row=8, column=0, sticky='w', pady=(9,0))
        ttk.Combobox(cfg, state='readonly', textvariable=self.mode_choice,
                     values=['個人モード', '双方向モード（双方のPCで実行）']).grid(row=9, column=0, columnspan=2, sticky='ew', pady=4)
        cfg.columnconfigure(0, weight=1)
        cfg.columnconfigure(1, weight=1)
        buttons = tk.Frame(cfg, bg='#1a2435')
        buttons.grid(row=10, column=0, columnspan=2, sticky='ew', pady=(12,0))
        self.start_button = tk.Button(buttons, text='●  翻訳開始', bg='#3485ff', fg='white', activebackground='#236bd2', relief='flat', padx=18, pady=10, command=self.toggle)
        self.start_button.pack(side='left')
        tk.Button(buttons, text='ブラウザでAWSログイン', command=self.begin_login,
                  padx=12, pady=8).pack(side='left', padx=8)
        tk.Button(buttons, text='デバイス更新', command=self.refresh_devices, padx=12, pady=8).pack(side='left', padx=8)
        tk.Button(buttons, text='字幕保存', command=self.save_history, padx=12, pady=8).pack(side='right')
        self.status = tk.StringVar(value='開始前 | AWS認証情報を入力するか既存のAWSプロファイルを使用')
        self.partial = tk.StringVar(value='')
        tk.Label(self.root, textvariable=self.status, bg='#101622', fg='#9fb2ce', anchor='w', font=('Segoe UI', 10)).pack(fill='x', padx=24)
        tk.Label(self.root, textvariable=self.partial, bg='#101622', fg='#e6d79d', anchor='w', wraplength=940, font=('Segoe UI', 10)).pack(fill='x', padx=24, pady=4)
        head = tk.Frame(self.root, bg='#101622')
        head.pack(fill='x', padx=22, pady=(14,4))
        tk.Label(head, text='リアルタイム字幕', bg='#101622', fg='white', font=('Segoe UI', 14, 'bold')).pack(side='left')
        tk.Label(head, text='原文 + 相手言語への翻訳（日本語 ⇄ 英語）', bg='#101622', fg='#9fb2ce').pack(side='right')
        container = tk.Frame(self.root, bg='#172133')
        container.pack(fill='both', expand=True, padx=22, pady=(4,20))
        scrollbar = tk.Scrollbar(container)
        scrollbar.pack(side='right', fill='y')
        self.output = tk.Text(container, bg='#172133', fg='#f2f6fc', relief='flat', padx=22, pady=18, wrap='word', font=('Segoe UI', 12), state='disabled', yscrollcommand=scrollbar.set, spacing3=8)
        self.output.pack(fill='both', expand=True)
        scrollbar.config(command=self.output.yview)
        self.output.tag_config('meta', foreground='#8fbaff', font=('Segoe UI', 10, 'bold'))
        self.output.tag_config('translation', foreground='#c3efd9', font=('Segoe UI', 13, 'bold'))
        self.output.tag_config('error', foreground='#ffb4b4')
        self.output.tag_config('plain', foreground='#f2f6fc')
        self.add_line('システム', 'スピーカーとマイクを選んで「翻訳開始」を押してください。', '', 'info')

    def begin_login(self):
        if self.active:
            messagebox.showwarning('録音中', '先に翻訳を停止してください。')
            return
        try:
            config = load_public_config()
            self.login_config = config
            self.region.set(config.get('runtime_region', config['region']))
        except Exception as exc:
            messagebox.showerror('Cognito設定', str(exc))
            return
        self.status.set('ブラウザでログインしてください（3分以内）')
        def task():
            try:
                credentials = browser_login(config)
                self.events.put(('login_success', credentials))
            except Exception as exc:
                self.events.put(('login_error', str(exc)))
        threading.Thread(target=task, daemon=True).start()

    def refresh_devices(self):
        try:
            self.speakers = sc.all_speakers()
            self.mics = sc.all_microphones(include_loopback=False)
            self.speaker_choice['values'] = [d.name for d in self.speakers]
            self.mic_choice['values'] = [d.name for d in self.mics]
            default_speaker = sc.default_speaker()
            default_mic = sc.default_microphone()
            if self.speakers:
                ix = next((i for i, s in enumerate(self.speakers) if s.id == default_speaker.id), 0)
                self.speaker_choice.current(ix)
            if self.mics:
                ix = next((i for i, m in enumerate(self.mics) if default_mic and m.id == default_mic.id), 0)
                self.mic_choice.current(ix)
        except Exception as ex:
            self.status.set(f'音声デバイス取得失敗: {ex}')

    def toggle(self):
        if self.active:
            self.stop.set()
            self.cancel_translations()
            self.active = False
            self.start_button.config(text='●  翻訳開始', bg='#3485ff')
            self.status.set('停止しています（処理中のリクエストは完了する場合があります）')
            self.partial.set('')
            for widget in self.credential_entries:
                widget.config(state='normal')
            return
        if self.speaker_choice.current() < 0 or self.mic_choice.current() < 0:
            messagebox.showerror('デバイス未選択', 'スピーカーとマイクを選択してください。')
            return
        if self.backend.current() == 1 and not os.getenv('OPENAI_API_KEY'):
            messagebox.showerror('OpenAI APIキー', 'OPENAI_API_KEY が必要です。')
            return
        try:
            region = self.region.get().strip()
            if not region:
                raise ValueError('AWSリージョンを入力してください。')
            access_key = self.access_key.get().strip()
            secret_key = self.secret_key.get().strip()
            session_token = self.session_token.get().strip()
            if bool(access_key) != bool(secret_key):
                raise ValueError('アクセスキーIDとシークレットアクセスキーは両方入力してください。')
            if session_token and not access_key:
                raise ValueError('セッショントークン使用時はアクセスキーIDとシークレットアクセスキーも必要です。')
            self.runtime_region = region
            self.runtime_credentials = (access_key, secret_key, session_token or None) if access_key else self.login_credentials
            if not self.runtime_credentials and self.login_config:
                raise ValueError('ブラウザログインを先に実行してください。')
            # Explicit credentials override existing profiles, but do not touch global environment variables.
            self.aws_session = make_aws_session(region, self.runtime_credentials)
            self.bedrock = self.aws_session.client('bedrock-runtime', config=__import__('botocore.config', fromlist=['Config']).Config(read_timeout=35, retries={'max_attempts': 2}))
            if self.backend.current() == 1:
                self.api_client = OpenAI(timeout=35.0, max_retries=1)
        except Exception as exc:
            messagebox.showerror('AWS設定エラー', str(exc))
            return
        self.stop = threading.Event()
        self.active = True
        for widget in self.credential_entries:
            widget.config(state='disabled')
        self.start_button.config(text='■  停止', bg='#b24c62')
        self.status.set(self.mode_choice.get() + ' · 音声取得中 · Bot不使用 · 相手側でもアプリを起動してください' if self.mode_choice.get().startswith('双方向') else '個人モード · 音声取得中 · Bot不使用')
        speaker = self.speakers[self.speaker_choice.current()]
        mic = self.mics[self.mic_choice.current()]
        for kind, source in [('相手（PC音声）', speaker), ('自分（マイク）', mic)]:
            if self.backend.current() == 0:
                threading.Thread(target=self.run_aws_stream, args=(kind, source, self.stop), daemon=True).start()
            else:
                audio_queue = queue.Queue(maxsize=12)
                threading.Thread(target=self.process_audio, args=(kind, audio_queue, self.stop), daemon=True).start()
                threading.Thread(target=self.capture, args=(kind, source, audio_queue, self.stop), daemon=True).start()

    def open_recording(self, kind, source, rate):
        if kind.startswith('相手'):
            device = sc.get_microphone(id=source.id, include_loopback=True)
            if not device:
                raise RuntimeError('ループバック録音デバイスが見つかりません')
        else:
            device = source
        return device.recorder(samplerate=rate)

    def run_aws_stream(self, kind, source, stop_event):
        try:
            asyncio.run(self.aws_stream(kind, source, stop_event))
        except Exception as exc:
            self.events.put(('error', kind, 'Amazon Transcribe: ' + str(exc)))

    async def aws_stream(self, kind, source, stop_event):
        from amazon_transcribe.client import TranscribeStreamingClient
        from amazon_transcribe.handlers import TranscriptResultStreamHandler
        from amazon_transcribe.model import TranscriptEvent
        from amazon_transcribe.auth import StaticCredentialResolver

        if self.runtime_credentials:
            resolver = StaticCredentialResolver(*self.runtime_credentials)
            client = TranscribeStreamingClient(region=self.runtime_region, credential_resolver=resolver)
        else:
            client = TranscribeStreamingClient(region=self.runtime_region)
        stream = await client.start_stream_transcription(
            language_code=None,
            identify_multiple_languages=True,
            language_options=['ja-JP', 'en-US'],
            media_sample_rate_hz=16000,
            media_encoding='pcm',
            enable_partial_results_stabilization=True,
            partial_results_stability='medium',
        )
        ui = self

        class CaptionHandler(TranscriptResultStreamHandler):
            async def handle_transcript_event(self, event: TranscriptEvent):
                for result in event.transcript.results:
                    if not result.alternatives:
                        continue
                    original = (result.alternatives[0].transcript or '').strip()
                    if not original or stop_event.is_set():
                        continue
                    if result.is_partial:
                        ui.events.put(('partial', kind, original))
                    else:
                        ui.events.put(('partial', kind, ''))
                        ui.schedule_translation(kind, original, stop_event)

        async def send_audio():
            try:
                with self.open_recording(kind, source, 16000) as recorder:
                    while not stop_event.is_set():
                        samples = await asyncio.to_thread(recorder.record, numframes=STREAM_FRAMES)
                        mono = np.asarray(samples, dtype=np.float32)
                        if mono.ndim == 2:
                            mono = mono.mean(axis=1)
                        pcm = (np.clip(mono, -1, 1) * 32767).astype('<i2')
                        await stream.input_stream.send_audio_event(audio_chunk=pcm.tobytes())
            finally:
                await stream.input_stream.end_stream()

        handler = CaptionHandler(stream.output_stream)
        sender = asyncio.create_task(send_audio())
        receiver = asyncio.create_task(handler.handle_events())
        done, pending = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.exception():
                raise task.exception()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def schedule_translation(self, kind, original, stop_event):
        # stop_event belongs to the session that produced the text, so a stopped session never sends to Bedrock.
        if stop_event.is_set():
            return
        try:
            self.translation_pool.submit(self.translate_with_bedrock, kind, original, stop_event, self.bedrock)
        except RuntimeError:
            pass  # pool was shut down by stop/close

    def cancel_translations(self):
        # Drop queued requests; requests already sent to Bedrock finish but are not displayed.
        self.translation_pool.shutdown(wait=False, cancel_futures=True)
        self.translation_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    def translate_with_bedrock(self, kind, original, stop_event, bedrock):
        if stop_event.is_set():
            return
        try:
            model_id = os.getenv('BEDROCK_MODEL_ID', 'jp.anthropic.claude-haiku-5-5')
            system_prompt = (
                'You are a real-time bilingual meeting interpreter. Detect if the text is primarily Japanese or English. '
                'Translate Japanese to English and English to Japanese faithfully. Preserve names, technical terms, and numbers. '
                'Do not explain or add notes. Reply with exactly two lines: first line ja or en (source language), '
                'second line the translated text. Do not add any other text.'
            )
            result = bedrock.converse(
                modelId=model_id,
                system=[{'text': system_prompt}],
                messages=[{'role': 'user', 'content': [{'text': original}]}],
                inferenceConfig={'maxTokens': 512},
            )
            blocks = result.get('output', {}).get('message', {}).get('content', [])
            text = ''.join(b.get('text', '') for b in blocks).strip()
            first, sep, translation = text.partition('\n')
            language = first.strip().lower()
            if language not in ('ja', 'en') or not sep:
                raise ValueError('Bedrock翻訳の応答形式が不正: ' + text[:160])
            if not stop_event.is_set():
                self.events.put(('caption', kind, original, translation.strip(), language))
        except Exception as ex:
            if not stop_event.is_set():
                self.events.put(('error', kind, 'Bedrock翻訳: ' + str(ex)))


    def capture(self, kind, source, audio_queue, stop_event):
        try:
            if kind.startswith('相手'):
                device = sc.get_microphone(id=source.id, include_loopback=True)
                if not device:
                    raise RuntimeError('ループバック録音デバイスが見つかりません')
            else:
                device = source
            with device.recorder(samplerate=RATE) as recorder:
                while not stop_event.is_set():
                    samples = recorder.record(numframes=RATE * CHUNK_SECONDS)
                    mono = np.asarray(samples, dtype=np.float32)
                    rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0
                    if rms < SILENCE_RMS:
                        continue
                    try:
                        audio_queue.put_nowait(mono)
                    except queue.Full:
                        self.events.put(('error', kind, 'API処理が追いつかず音声の一部を破棄しました'))
        except Exception as ex:
            self.events.put(('error', kind, str(ex)))

    def process_audio(self, kind, audio_queue, stop_event):
        while not stop_event.is_set():
            try:
                audio = audio_queue.get(timeout=0.4)
            except queue.Empty:
                continue
            self.transcribe_and_translate(kind, audio, stop_event)

    def transcribe_and_translate(self, kind, audio, stop_event):
        try:
            transcription = self.api_client.audio.transcriptions.create(
                model='gpt-4o-mini-transcribe', file=to_wav(audio), response_format='json'
            )
            original = (transcription.text or '').strip()
            if original and not stop_event.is_set():
                self.schedule_translation(kind, original, stop_event)
        except Exception as ex:
            self.events.put(('error', kind, 'OpenAI音声認識: ' + str(ex)))

    def add_line(self, label, original, translation, language):
        self.output.config(state='normal')
        stamp = datetime.now().strftime('%H:%M:%S')
        self.output.insert('end', f'{stamp}  {label}  [{language}]\n', 'meta')
        self.output.insert('end', original + '\n', 'plain')
        if translation:
            self.output.insert('end', translation + '\n', 'translation')
        self.output.insert('end', '\n')
        self.output.see('end')
        self.output.config(state='disabled')

    def process_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == 'caption':
                    _, source, original, translation, language = event
                    self.add_line(source, original, translation, language)
                    self.history.append({'time': datetime.now().isoformat(timespec='seconds'), 'source': source, 'language': language, 'original': original, 'translation': translation})
                elif event[0] == 'partial':
                    _, source, fragment = event
                    self.partial.set(f'認識中 [{source}]: {fragment}' if fragment else '')
                elif event[0] == 'login_success':
                    self.login_credentials = event[1]
                    self.status.set('ブラウザ認証成功：一時AWS認証情報を取得しました。翻訳開始できます。')
                elif event[0] == 'login_error':
                    self.status.set('ブラウザ認証失敗: ' + event[1])
                    messagebox.showerror('ログイン失敗', event[1])
                elif event[0] == 'error':
                    _, source, error = event
                    self.add_line('エラー: ' + source, error, '', 'error')
                    self.status.set('録音/通信エラー。内容を確認してください。')
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)

    def save_history(self):
        if not self.history:
            messagebox.showinfo('字幕がありません', '保存できる字幕がありません。')
            return
        filename = filedialog.asksaveasfilename(title='字幕履歴保存', defaultextension='.json', filetypes=[('JSON', '*.json')], initialfile='meeting-captions-' + datetime.now().strftime('%Y%m%d-%H%M') + '.json')
        if filename:
            Path(filename).write_text(json.dumps(self.history, ensure_ascii=False, indent=2), encoding='utf-8')
            self.status.set('字幕を保存しました: ' + filename)

    def close(self):
        self.stop.set()
        self.translation_pool.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


if __name__ == '__main__':
    if os.getenv('MEETING_TRANSLATOR_SMOKE_TEST'):
        # Packaging check: load lazily imported modules too, then exit without opening the UI.
        import amazon_transcribe.auth, amazon_transcribe.client, amazon_transcribe.handlers, amazon_transcribe.model  # noqa: F401
        raise SystemExit(0)
    app = tk.Tk()
    TranslatorUI(app)
    app.mainloop()

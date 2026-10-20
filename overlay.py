"""Floating caption overlay shown while translating: a control pill above a two-column
(original | translation) panel that stays on top of the meeting window."""
import ctypes
import sys
import time
import tkinter as tk

PANEL_BG = '#1b2433'
PILL_BG = '#f7f8fa'
DIVIDER = '#3a475c'
MAX_CAPTIONS = 40
SOURCES = ('相手（PC音声）', '自分（マイク）')
# Newest captions are brightest; older ones fade toward the background.
COLORS = {
    '相手（PC音声）': ('#ffffff', '#c9d2df', '#7c879a'),
    '自分（マイク）': ('#a9d4ff', '#84a8cc', '#5a7290'),
}
PARTIAL_COLOR = '#8e99ab'


def fade_level(age):
    return 0 if age < 2 else 1 if age < 4 else 2


def virtual_screen_top():
    """Top edge of the whole desktop (negative when a monitor sits above the primary one)."""
    if sys.platform == 'win32':
        try:
            return ctypes.windll.user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        except Exception:
            pass
    return 0


def round_corners(window):
    """Windows 11 rounded corners for a borderless window; silently ignored elsewhere."""
    if sys.platform != 'win32':
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
    except Exception:
        pass


class CaptionOverlay:
    def __init__(self, root, font_family, on_stop, on_settings, on_source_toggle):
        self.root = root
        self.on_stop = on_stop
        self.on_settings = on_settings
        self.on_source_toggle = on_source_toggle
        self.captions = []  # (kind, original, translation)
        self.partial = None  # (kind, text)
        self.enabled = {kind: True for kind in SOURCES}
        self.started_at = None
        self._render_job = None
        self._timer_job = None
        self._drag = None
        self.family = font_family
        self.font = (font_family, 17, 'bold')
        self._build_panel(font_family)
        self._build_pill(font_family)
        self.hide()

    # ---- construction -------------------------------------------------------------------
    def _popup(self, bg, alpha):
        window = tk.Toplevel(self.root, bg=bg)
        window.overrideredirect(True)
        window.attributes('-topmost', True)
        try:
            window.attributes('-alpha', alpha)
        except tk.TclError:
            pass
        return window

    def _build_panel(self, family):
        self.panel = panel = self._popup(PANEL_BG, 0.97)  # no blur in Tk, so keep text behind barely visible
        header = tk.Frame(panel, bg=PANEL_BG)
        header.pack(fill='x', padx=26, pady=(16, 6))
        tk.Label(header, text='English  ⇄  日本語', bg=PANEL_BG, fg='#f2f5fa',
                 font=(family, 13, 'bold')).pack(side='left')
        tk.Button(header, text='×', command=self.on_stop, bg=PANEL_BG, fg='#dfe5ee', activebackground='#2a3547',
                  activeforeground='white', relief='flat', bd=0, font=(family, 15), cursor='hand2').pack(side='right')
        tk.Button(header, text='⚙', command=self.on_settings, bg=PANEL_BG, fg='#dfe5ee', activebackground='#2a3547',
                  activeforeground='white', relief='flat', bd=0, font=(family, 13), cursor='hand2').pack(side='right', padx=(0, 14))
        for kind, label in reversed(list(zip(SOURCES, ('相手', '自分')))):  # packed right-to-left
            tk.Label(header, text='● ' + label, bg=PANEL_BG, fg=COLORS[kind][0],
                     font=(family, 10, 'bold')).pack(side='right', padx=(0, 14))
        self.status = tk.Label(header, text='', bg=PANEL_BG, fg='#ffb4b4', font=(family, 10))
        self.status.pack(side='right', padx=(0, 14))
        grip = tk.Label(header, text='⠿', bg='#2c3850', fg='#c4cede', padx=18, cursor='fleur')
        grip.pack(side='top')
        for widget in (header, grip):
            self._bind_drag(widget)

        body = tk.Frame(panel, bg=PANEL_BG)
        body.pack(fill='both', expand=True, padx=(26, 18), pady=(0, 6))
        options = dict(bg=PANEL_BG, fg='white', relief='flat', bd=0, highlightthickness=0, wrap='word',
                       font=self.font, spacing1=2, spacing3=2, cursor='arrow', takefocus=0)
        self.left = tk.Text(body, **options)
        self.right = tk.Text(body, **options)
        self.left.grid(row=0, column=0, sticky='nsew')
        tk.Frame(body, bg=DIVIDER, width=1).grid(row=0, column=1, sticky='ns', padx=18)
        self.right.grid(row=0, column=2, sticky='nsew')
        body.columnconfigure(0, weight=1, uniform='cols')
        body.columnconfigure(2, weight=1, uniform='cols')
        body.rowconfigure(0, weight=1)
        for text in (self.left, self.right):
            for kind, colors in COLORS.items():
                for level, color in enumerate(colors):
                    text.tag_config(f'{kind}{level}', foreground=color)
            text.tag_config('partial', foreground=PARTIAL_COLOR)
            text.tag_config('gap', font=(family, 6))
            text.config(state='disabled')
        self.left.bind('<Configure>', lambda _e: self.schedule_render())

        resize = tk.Label(panel, text='◢', bg=PANEL_BG, fg='#55627a', cursor='size_nw_se')
        resize.place(relx=1.0, rely=1.0, anchor='se', x=-6, y=-4)
        resize.bind('<ButtonPress-1>', self._start_resize)
        resize.bind('<B1-Motion>', self._resize)

    def _build_pill(self, family):
        self.pill = pill = self._popup(PILL_BG, 0.97)
        inner = tk.Frame(pill, bg=PILL_BG)
        inner.pack(padx=18, pady=8)
        self.timer = tk.Label(inner, text='00:00', bg=PILL_BG, fg='#2b3442', font=(family, 13))
        self.timer.pack(side='left', padx=(0, 12))
        tk.Button(inner, text='■', command=self.on_stop, bg='#e5262f', fg='white', activebackground='#c41d25',
                  activeforeground='white', relief='flat', bd=0, width=2, font=(family, 11, 'bold'),
                  cursor='hand2').pack(side='left', padx=(0, 10))
        self.source_buttons = {}
        for kind, label in zip(SOURCES, ('🔊 相手', '🎤 自分')):
            button = tk.Button(inner, text=label, bg=PILL_BG, relief='flat', bd=0, font=(family, 11),
                               activebackground='#e6e9ef', cursor='hand2',
                               command=lambda k=kind: self.toggle_source(k))
            button.pack(side='left', padx=6)
            self.source_buttons[kind] = button
        tk.Button(inner, text='⚙', command=self.on_settings, bg=PILL_BG, fg='#2b3442', relief='flat', bd=0,
                  activebackground='#e6e9ef', font=(family, 12), cursor='hand2').pack(side='left', padx=6)
        tk.Frame(inner, bg='#d5dae3', width=1, height=22).pack(side='left', padx=8)
        grip = tk.Label(inner, text='⠿', bg=PILL_BG, fg='#6b7585', font=(family, 13), cursor='fleur')
        grip.pack(side='left')
        for widget in (pill, inner, self.timer, grip):
            self._bind_drag(widget)
        self._refresh_source_buttons()

    # ---- public API ---------------------------------------------------------------------
    def show(self):
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width = min(1200, int(screen_w * 0.72))
        height = 300
        x, y = (screen_w - width) // 2, screen_h - height - 90
        self.panel.geometry(f'{width}x{height}+{x}+{y}')
        for window in (self.panel, self.pill):
            window.deiconify()
            window.lift()
            round_corners(window)
        self._place_pill()
        self.started_at = time.monotonic()
        self._tick()
        self.render()

    def hide(self):
        if self._timer_job:
            self.root.after_cancel(self._timer_job)
            self._timer_job = None
        self.panel.withdraw()
        self.pill.withdraw()

    def reset(self):
        """Start of a new session: empty captions, all sources visible again."""
        self.captions.clear()
        self.partial = None
        self.enabled = {kind: True for kind in SOURCES}
        self._refresh_source_buttons()
        self.status.config(text='')
        self.render()

    def add_caption(self, kind, original, translation):
        self.captions.append((kind, original, translation))
        del self.captions[:-MAX_CAPTIONS]
        if self.partial and self.partial[0] == kind:
            self.partial = None
        self.schedule_render()

    def set_partial(self, kind, text):
        if text:
            self.partial = (kind, text)
        elif self.partial and self.partial[0] == kind:
            self.partial = None  # only clear this source's in-progress text
        self.schedule_render()

    def set_status(self, text):
        self.status.config(text=text[:60])

    def toggle_source(self, kind):
        self.enabled[kind] = not self.enabled[kind]
        self._refresh_source_buttons()
        self.on_source_toggle(kind, self.enabled[kind])

    # ---- rendering ----------------------------------------------------------------------
    def schedule_render(self):
        if self._render_job is None:
            self._render_job = self.root.after(30, self.render)

    def render(self):
        """Redraw both columns, padding the shorter side so each original lines up with its translation."""
        self._render_job = None
        for text in (self.left, self.right):
            text.config(state='normal')
            text.delete('1.0', 'end')
        count = len(self.captions)
        for index, (kind, original, translation) in enumerate(self.captions):
            tag = f'{kind}{fade_level(count - 1 - index)}'
            self._append_pair(original, translation, tag)
        if self.partial:
            self._append_pair(self.partial[1], '…', 'partial')
        for text in (self.left, self.right):
            text.config(state='disabled')
            text.see('end')

    def _append_pair(self, original, translation, tag):
        starts = (self.left.index('end-1c'), self.right.index('end-1c'))
        self.left.insert('end', original + '\n', tag)
        self.right.insert('end', translation + '\n', tag)
        lines = [self._display_lines(text, start) for text, start in zip((self.left, self.right), starts)]
        if lines[0] != lines[1]:
            shorter = self.left if lines[0] < lines[1] else self.right
            shorter.insert('end', '\n' * abs(lines[0] - lines[1]), tag)
        for text in (self.left, self.right):
            text.insert('end', '\n', 'gap')

    @staticmethod
    def _display_lines(text, start):
        text.update_idletasks()
        result = text.count(start, 'end-1c', 'displaylines')
        return result[0] if isinstance(result, tuple) else (result or 0)

    def _refresh_source_buttons(self):
        for kind, button in self.source_buttons.items():
            button.config(fg='#2b3442' if self.enabled[kind] else '#a3abb8',
                          font=(self.family, 11) if self.enabled[kind] else (self.family, 11, 'overstrike'))

    def _tick(self):
        elapsed = int(time.monotonic() - (self.started_at or time.monotonic()))
        self.timer.config(text=f'{elapsed // 60:02d}:{elapsed % 60:02d}')
        self._timer_job = self.root.after(1000, self._tick)

    # ---- moving / resizing --------------------------------------------------------------
    def _place_pill(self):
        self.pill.update_idletasks()
        x = self.panel.winfo_x() + (self.panel.winfo_width() - self.pill.winfo_reqwidth()) // 2
        y = self.panel.winfo_y() - self.pill.winfo_reqheight() - 12
        if y < virtual_screen_top():  # no room above the desktop edge: put the pill under the panel
            y = self.panel.winfo_y() + self.panel.winfo_height() + 12
        self.pill.geometry(f'+{x}+{y}')

    def _bind_drag(self, widget):
        widget.bind('<ButtonPress-1>', self._start_drag, add='+')
        widget.bind('<B1-Motion>', self._drag_move, add='+')

    def _start_drag(self, event):
        self._drag = (event.x_root - self.panel.winfo_x(), event.y_root - self.panel.winfo_y())

    def _drag_move(self, event):
        if not self._drag:
            return
        self.panel.geometry(f'+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}')
        self._place_pill()

    def _start_resize(self, event):
        self._resize_origin = (event.x_root, event.y_root, self.panel.winfo_width(), self.panel.winfo_height())

    def _resize(self, event):
        x0, y0, w0, h0 = self._resize_origin
        width, height = max(520, w0 + event.x_root - x0), max(180, h0 + event.y_root - y0)
        self.panel.geometry(f'{width}x{height}')
        self._place_pill()

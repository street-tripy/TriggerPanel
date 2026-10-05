#!/usr/bin/env python3
"""TriggerPanel — trigger actions on this PC from your phone (iOS Shortcuts).

Native PyQt6 desktop app (dark theme, styled like TradingBot) with a
system-tray icon, plus a tiny HTTP server:

  GET  /                     read-only status page
  GET  /run?app=<id>&token=  run the entry with that index (used by Shortcuts)
  GET  /list?token=          entries + link to the Apple-style panel page
  GET  /panel?token=         hosted button page (icons, one tap = trigger)
  GET  /health               liveness probe

Entries come in five types — created via a frameless wizard overlay:
  app    — launch an .exe / .lnk / URI / command line
  script — run a .py file with its own virtualenv's interpreter
  keys   — press a keystroke or a series of keystrokes (SendInput)
  http   — call an API endpoint (GET/POST/PUT/PATCH/DELETE)
  func   — run a built-in action (see functions.py: monitor brightness),
           which discovers the displays itself — nothing per-machine is stored

Closing or minimizing the main window hides it to the system tray.
Config lives in config.json; log output goes to server.log.
"""

import ctypes
import functions  # built-in actions (sibling module — also runnable on its own)
import html
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from PyQt6.QtCore import QBuffer, QEvent, QIODevice, QPointF, QSize, Qt, QTimer
from PyQt6.QtGui import (QBrush, QColor, QFont, QIcon, QPainter, QPen,
                         QPolygonF, QPixmap)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                             QComboBox,
                             QGraphicsDropShadowEffect, QGroupBox, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMenu,
                             QMessageBox, QPushButton, QStackedWidget,
                             QSpinBox,
                             QSystemTrayIcon, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

FROZEN = bool(getattr(sys, "frozen", False))  # set by PyInstaller
if FROZEN:
    # Onefile: __file__ lives in the extraction temp dir — keep config/log
    # next to the actual .exe instead (exe sits at the project root).
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "server.log")

# No console window flash when a command-line app is launched from pythonw.
CREATE_NO_WINDOW = 0x08000000

# Anything with a URI scheme: http://, spotify:, ms-settings:, shell:AppsFolder\...
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")

CONFIG_LOCK = threading.Lock()

# PNG bytes of the app icon (served at /icon for the panel's apple-touch-icon).
ICON_PNG: bytes | None = None

# Set when the GUI asks to relaunch the process (e.g. after a port change);
# main() checks it after the event loop exits and starts a fresh instance.
_RESTART = {"pending": False}

ENTRY_TYPES = ("app", "script", "keys", "http", "func")

APP_VERSION = "1.5.0"
SETTINGS_ICON = "settings.png"   # title-bar gear (from TradingBot ui/icons)

# ── TradingBot palette (Catppuccin Mocha) ─────────────────────────────────────
CRUST = "#11111b"     # title bar
BASE = "#181825"      # window background
MANTLE = "#1e1e2e"    # dialogs / overlay card
SURFACE0 = "#313244"  # headers, hover
SURFACE1 = "#45475a"  # button background, borders
SURFACE2 = "#585b70"  # button hover
TEXT = "#cdd6f4"
SUBTEXT = "#a6adc8"
OVERLAY = "#6c7086"   # muted text / overlay light border
BLUE = "#89b4fa"      # accent
GREEN = "#a6e3a1"
RED = "#f38ba8"
YELLOW = "#f9e2af"
PINK = "#f78199"      # built-in functions

STYLESHEET = f"""
TriggerWindow {{ background: {BASE}; }}
TitleBar {{ background: {CRUST}; }}
QLabel {{ color: {TEXT}; background: transparent; }}
QLabel#muted {{ color: {OVERLAY}; font-size: 11px; }}
QLabel#wizardHint {{ color: {OVERLAY}; font-size: 11px; }}
QLabel#stepLbl {{ color: {SUBTEXT}; background: {MANTLE}; font-size: 11px;
  font-weight: bold; }}
QLabel#statusOk {{ color: {GREEN}; font-weight: bold; }}
QLabel#statusWarn {{ color: {RED}; font-weight: bold; }}
QLabel#statusInfo {{ color: {BLUE}; font-weight: bold; }}
QLabel#panelLink a {{ color: {BLUE}; text-decoration: none; }}
QLabel#panelLink a:hover {{ text-decoration: underline; }}
QPushButton#settingsBtn {{ background: transparent; border: none;
  color: {OVERLAY}; padding: 0; border-radius: 0; }}
QPushButton#settingsBtn:hover {{ background: {SURFACE0}; }}
QLineEdit {{
  background: {CRUST}; color: {TEXT}; border: 1px solid {SURFACE1};
  border-radius: 4px; padding: 5px 8px; font-family: Consolas, monospace;
  selection-background-color: {BLUE}; selection-color: {CRUST};
}}
QLineEdit:focus {{ border-color: {BLUE}; }}
QLineEdit:read-only {{ color: {TEXT}; }}
QPushButton {{
  background: {SURFACE1}; color: {TEXT}; border: 1px solid {SURFACE2};
  padding: 6px 14px; border-radius: 4px;
}}
QPushButton:hover {{ background: {SURFACE2}; }}
QPushButton:pressed {{ background: {SURFACE0}; }}
QPushButton:disabled {{ color: {SURFACE2}; background: {SURFACE0}; border: none; }}
QPushButton#accent {{ background: {BLUE}; color: {CRUST}; border: none;
                      font-weight: bold; }}
QPushButton#accent:hover {{ background: #b4d3fb; }}
QPushButton#danger {{ color: {RED}; background: {SURFACE0};
                      border: 1px solid {SURFACE1}; }}
QPushButton#danger:hover {{ background: {SURFACE1}; }}
QPushButton#titleBtn {{ background: transparent; border: none; color: {OVERLAY};
                        font-size: 13px; padding: 0 11px; border-radius: 0; }}
QPushButton#titleBtn:hover {{ background: {SURFACE0}; color: {TEXT}; }}
QPushButton#titleClose {{ background: transparent; border: none; color: {OVERLAY};
                          font-size: 13px; padding: 0; border-radius: 0; }}
QPushButton#titleClose:hover {{ background: {RED}; color: {CRUST}; }}
QTreeWidget {{
  background: {BASE}; color: {TEXT}; border: 1px solid {SURFACE1};
  font-size: 12px; outline: none;
}}
QTreeWidget::item {{ padding: 5px 8px; }}
QTreeWidget::item:selected {{ background: {SURFACE1}; }}
QTreeWidget::item:hover {{ background: {SURFACE0}; }}
QHeaderView::section {{
  background: {SURFACE0}; color: {TEXT}; border: none;
  border-right: 1px solid {SURFACE1}; border-bottom: 1px solid {SURFACE1};
  padding: 5px 8px; font-weight: bold;
}}
QGroupBox {{
  color: {SUBTEXT}; border: 1px solid {SURFACE1}; border-radius: 6px;
  margin-top: 10px; padding-top: 12px; font-weight: bold;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}
QScrollBar:vertical {{ background: {BASE}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {SURFACE1}; border-radius: 5px;
                               min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {SURFACE2}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QMenu {{ background: {SURFACE0}; color: {TEXT}; border: 1px solid {SURFACE1};
         padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 3px; }}
QMenu::item:selected {{ background: {SURFACE1}; }}
QMessageBox {{ background: {MANTLE}; }}
QMessageBox QLabel {{ color: {TEXT}; background: transparent; }}
"""

WIZARD_STYLESHEET = f"""
NewEntryOverlay {{ background: {MANTLE}; border: 1px solid {OVERLAY}; }}
SettingsOverlay {{ background: {MANTLE}; border: 1px solid {OVERLAY}; }}
QStackedWidget {{ background: {MANTLE}; }}
QWidget#page {{ background: {MANTLE}; }}
QCheckBox {{ color: {TEXT}; spacing: 8px; font-size: 13px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {SURFACE2};
  border-radius: 4px; background: {BASE}; }}
QCheckBox::indicator:checked {{ background: {BLUE}; border-color: {BLUE}; }}
QPushButton#typeCard {{ background: {BASE}; color: {TEXT};
  border: 1px solid {SURFACE1}; border-radius: 8px;
  padding: 12px 14px; font-size: 13px; text-align: left; }}
QPushButton#typeCard:hover {{ background: {SURFACE0}; border-color: {BLUE}; }}
QComboBox {{
  background: {SURFACE0}; color: {TEXT}; border: 1px solid {SURFACE1};
  border-radius: 4px; padding: 5px 8px; font-size: 12px;
}}
QComboBox:hover {{ border-color: {SURFACE2}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
  background: {SURFACE0}; color: {TEXT};
  selection-background-color: {SURFACE1};
}}
"""


# --------------------------------------------------------------------------- utils

def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if sys.stdout is not None:  # pythonw has no console
        print(line, flush=True)


class _LogWriter:
    """Captures stderr under pythonw (where sys.stderr is None) into server.log."""

    def write(self, s):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(s)
        except OSError:
            pass

    def flush(self):
        pass


if sys.stderr is None:
    sys.stderr = _LogWriter()


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        cfg = {"port": 8765, "token": secrets.token_urlsafe(16), "apps": []}
        cfg["next_id"] = 1
        _write_config(cfg)
        log(f"created new config at {CONFIG_PATH}")
        return cfg
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("port", 8765)
    cfg.setdefault("apps", [])
    # Monotonic id counter so indexes are never reused, even after every
    # app has been deleted (max(existing ids) would reset to 1 there).
    cfg.setdefault("next_id", max((a["id"] for a in cfg["apps"]), default=0) + 1)
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(16)
        _write_config(cfg)
    return cfg


def _write_config(cfg: dict) -> None:
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


def find_app(cfg: dict, app_id: int) -> dict | None:
    for app in cfg["apps"]:
        if app["id"] == app_id:
            return app
    return None


def entry_type(app: dict) -> str:
    """Type of an entry; migrates pre-type configs on the fly."""
    t = app.get("type")
    if t in ENTRY_TYPES:
        return t
    if "keys" in app:
        return "keys"
    if "url" in app:
        return "http"
    return "app"


def entry_details(app: dict) -> str:
    """Short human-readable detail shown in the list's Details column."""
    t = entry_type(app)
    if t == "app":
        return str(app.get("path", ""))
    if t == "script":
        return str(app.get("script", ""))
    if t == "keys":
        return str(app.get("keys", ""))
    if t == "func":
        fn = functions.FUNCTIONS.get(str(app.get("func", "")))
        name = fn["label"] if fn else str(app.get("func", ""))
        try:
            return f"{name} — {fn['detail'](app)}" if fn else name
        except Exception:
            return name
    return f"{app.get('method', 'GET')} {app.get('url', '')}"


def store_entry(fields: dict) -> int:
    """Persist a new entry under the next permanent id and return it."""
    with CONFIG_LOCK:
        cfg = load_config()
        new_id = cfg["next_id"]
        cfg["next_id"] = new_id + 1
        entry = {"id": new_id, "created": f"{datetime.now():%Y-%m-%d %H:%M:%S}"}
        entry.update(fields)
        entry["id"] = new_id  # fields can't override the id
        cfg["apps"].append(entry)
        _write_config(cfg)
    log(f"added #{new_id} [{fields.get('type', 'app')}] {fields.get('name')!r}")
    return new_id


def update_entry(entry_id: int, fields: dict) -> None:
    """Replace an existing entry's content, keeping its permanent id."""
    with CONFIG_LOCK:
        cfg = load_config()
        old = find_app(cfg, entry_id)
        if old is None:
            raise KeyError(f"no entry #{entry_id}")
        updated = {"id": entry_id, "created": old.get("created", "")}
        updated.update(fields)
        updated["id"] = entry_id  # fields can't override the id
        cfg["apps"] = [updated if a["id"] == entry_id else a
                       for a in cfg["apps"]]
        _write_config(cfg)
    log(f"updated #{entry_id} -> [{fields.get('type', '')}] "
        f"{fields.get('name')!r}")


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def run_url(cfg: dict, app_id: int) -> str:
    """Shortcut URL using this PC's LAN IP address."""
    return f"http://{lan_ip()}:{cfg['port']}/run?app={app_id}&token={cfg['token']}"


# --------------------------------------------------------------------------- actions

def launch(app: dict) -> None:
    target = str(app.get("path", "")).strip()
    if not target:
        raise FileNotFoundError("empty path")
    if os.path.exists(target):
        os.startfile(os.path.abspath(target))
        return
    # Bare URI (spotify:, ms-settings:, shell:AppsFolder\..., https://...)
    # or a single-token path — startfile raises for a bad path.
    if " " not in target and SCHEME_RE.match(target):
        os.startfile(target)
        return
    # Command line like "C:\Some App\app.exe" --flag — verify the executable
    # exists so a typo surfaces as an error instead of a silent hidden window.
    m = re.match(r'^"([^"]+)"|^(\S+)', target)
    first = (m.group(1) or m.group(2)) if m else ""
    is_pathlike = (
        (len(first) >= 2 and first[1] == ":" and first[0].isalpha())
        or (os.path.sep in first)
        or (os.path.altsep is not None and os.path.altsep in first)
    )
    if first and is_pathlike and not os.path.exists(first):
        raise FileNotFoundError(f"not found: {first}")
    subprocess.Popen(target, shell=True, creationflags=CREATE_NO_WINDOW)


# ── keystrokes (SendInput) ────────────────────────────────────────────────────

_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002

# Named keys → virtual-key codes (case-insensitive lookup on lowercase).
_NAMED_KEYS = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22,
    "pgdn": 0x22, "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "printscreen": 0x2C, "insert": 0x2D,
    "delete": 0x2E, "ctrl": 0x11, "control": 0x11, "shift": 0x10,
    "alt": 0x12, "menu": 0x12, "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
    "scrolllock": 0x91, "numlock": 0x93,
}

# Punctuation spellings resolve through the real character path (VkKeyScanW)
# — ord('+')=43 etc. are NOT virtual-key codes (ord('[')=91 would press Win!).
_CHAR_ALIASES = {
    "plus": "+", "comma": ",", "minus": "-", "period": ".", "slash": "/",
    "backslash": "\\", "semicolon": ";", "quote": "'",
    "lbracket": "[", "rbracket": "]", "backquote": "`",
}
for _i in range(1, 25):
    _NAMED_KEYS[f"f{_i}"] = 0x70 + _i - 1
for _i in range(10):
    _NAMED_KEYS[f"numpad{_i}"] = 0x60 + _i

# VKs that need KEYEVENTF_EXTENDEDKEY on Windows.
_EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
                 0x2D, 0x2E, 0x5B, 0x5C, 0xA3, 0xA4, 0x6F}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_uint),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_ubyte * 32)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _INPUTUNION)]


def _resolve_key(token: str) -> tuple[int, int]:
    """Resolve one key token → (vk, extra_shift_flag).

    extra_shift_flag is 0x10 when the character itself needs Shift
    (e.g. 'A' or '+' on a US layout).
    """
    if not token:
        raise ValueError("empty key in sequence")
    low = token.lower()
    if low in _CHAR_ALIASES:          # punctuation names → real char path
        token = _CHAR_ALIASES[low]
        low = token.lower()
    if low in _NAMED_KEYS:
        return _NAMED_KEYS[low], 0
    if len(token) == 1:
        ch = token
        if ch.isalpha() or ch.isdigit():
            shift = 0x10 if (ch.isalpha() and ch.isupper()) else 0
            return ord(ch.upper()), shift
        r = ctypes.windll.user32.VkKeyScanW(ord(ch))
        if r == -1 or r == 0xFFFF:
            raise ValueError(f"cannot type {ch!r} on this keyboard layout")
        vk = r & 0xFF
        shift = 0x10 if (r & 0x100) else 0
        return vk, shift
    raise ValueError(f"unknown key {token!r} — valid: a-z, 0-9, f1-f24, "
                     "enter, tab, esc, space, up/down/left/right, home, end, "
                     "pageup, pagedown, insert, delete, ctrl, shift, alt, win, "
                     "plus, comma, minus, period, …")


def parse_keys(seq: str) -> list[list[tuple[int, int]]]:
    """Parse 'ctrl+shift+s, enter' → groups of (vk, extra_shift) press-sets.

    '+' combines keys within one combo; ',' separates timed steps.
    """
    seq = (seq or "").strip()
    if not seq:
        raise ValueError("empty keystroke sequence")
    groups = []
    for raw_group in seq.split(","):
        raw_group = raw_group.strip()
        if not raw_group:
            continue
        tokens = raw_group.split("+")
        if "" in tokens:                     # 'ctrl++' → literal plus key
            tokens = [t for t in tokens if t]
            tokens.append("plus")
        press: list[tuple[int, int]] = []
        for tok in tokens:
            press.append(_resolve_key(tok.strip()))
        if not press:
            raise ValueError(f"empty combo in {raw_group!r}")
        groups.append(press)
    if not groups:
        raise ValueError("empty keystroke sequence")
    return groups


def _key_input(vk: int, keyup: bool) -> _INPUT:
    inp = _INPUT()
    inp.type = _INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    inp.union.ki.dwFlags = _KEYEVENTF_KEYUP if keyup else 0
    if vk in _EXTENDED_VKS:
        inp.union.ki.dwFlags |= _KEYEVENTF_EXTENDEDKEY
    return inp


def send_keys(seq: str) -> int:
    """Press a key sequence via SendInput. Returns number of events sent.

    Keys go to whatever window currently has focus on this PC.
    """
    groups = parse_keys(seq)
    total = 0
    for gi, press in enumerate(groups):
        events: list[_INPUT] = []
        # Full press-set: implied Shift first, duplicates collapsed so the
        # key-down / key-up counts always balance.
        downs: list[int] = []
        for vk, extra_shift in press:
            for v in ([0x10] if extra_shift else []) + [vk]:
                if v not in downs:
                    downs.append(v)
        for vk in downs:
            events.append(_key_input(vk, keyup=False))
        for vk in reversed(downs):
            events.append(_key_input(vk, keyup=True))
        arr = (_INPUT * len(events))(*events)
        sent = ctypes.windll.user32.SendInput(
            len(events), ctypes.byref(arr), ctypes.sizeof(_INPUT))
        if sent != len(events):
            raise RuntimeError(
                f"SendInput delivered {sent}/{len(events)} events — the focused "
                "window may be running as administrator (Windows UIPI blocks "
                "injection)")
        total += sent
        if gi < len(groups) - 1:
            time.sleep(0.07)  # pause between comma-separated steps
    return total


# ── http ──────────────────────────────────────────────────────────────────────

def http_call(app: dict) -> int:
    """Call the entry's API endpoint. Returns the HTTP status code.

    4xx/5xx still count as delivered (the server answered); connection
    failures / timeouts raise and surface as an error.
    """
    url = str(app.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"URL must start with http:// or https:// — got {url!r}")
    method = str(app.get("method") or "GET").upper()
    body = str(app.get("body") or "")
    data = body.encode("utf-8") if body and method in ("POST", "PUT", "PATCH") else None
    headers = {"User-Agent": "TriggerPanel/3.0"}
    if data is not None and body.lstrip().startswith(("{", "[")):
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code  # endpoint answered — the call happened


def _resolve_venv_python(venv: str, script: str) -> str:
    """Find the interpreter for a script entry: explicit venv or auto-detect."""
    if venv:
        if os.path.isdir(venv):
            cand = os.path.join(venv, "Scripts", "python.exe")
            if not os.path.isfile(cand):
                raise FileNotFoundError(f"no python.exe in venv: {venv}")
            return cand
        if os.path.isfile(venv):
            return venv
        raise FileNotFoundError(f"venv not found: {venv}")
    here = os.path.dirname(os.path.abspath(script))
    for base in (here, os.path.dirname(here)):
        for name in ("venv", ".venv"):
            cand = os.path.join(base, name, "Scripts", "python.exe")
            if os.path.isfile(cand):
                return cand
    raise FileNotFoundError(
        "no venv found next to the script — set one on the entry "
        "(path to the venv folder or its python.exe)")


def run_script(app: dict) -> str:
    """Run a Python script with its own virtualenv's interpreter."""
    script = str(app.get("script", "")).strip()
    if not script:
        raise FileNotFoundError("empty script path")
    if not os.path.isfile(script):
        raise FileNotFoundError(f"not found: {script}")
    py = _resolve_venv_python(str(app.get("venv", "") or "").strip(), script)
    cwd = os.path.dirname(os.path.abspath(script))
    subprocess.Popen([py, script], cwd=cwd, creationflags=CREATE_NO_WINDOW)
    return f"ran {os.path.basename(script)} with {py}"


def icon_search(words: str, limit: int = 6) -> list:
    """Ask Iconify for icon ids matching words — the same service the phone
    panel renders through, so anything offered here is directly usable."""
    words = (words or "").strip()
    if not words:
        return []
    url = "https://api.iconify.design/search?limit=%d&query=%s" % (
        limit, quote(words))
    req = urllib.request.Request(url, headers={"User-Agent": "TriggerPanel"})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return []          # offline / blocked: the caller says so plainly
    return [i for i in data.get("icons", []) if isinstance(i, str)]


def execute(app: dict) -> str:
    """Run an entry by type. Returns a short detail string for UI/JSON."""
    t = entry_type(app)
    if t == "app":
        launch(app)
        return f"launched {app.get('path', '')}"
    if t == "script":
        return run_script(app)
    if t == "keys":
        n = send_keys(str(app.get("keys", "")))
        return f"sent {n} key events ({app.get('keys')})"
    if t == "http":
        status = http_call(app)
        return f"{app.get('method', 'GET')} {app.get('url')} → HTTP {status}"
    if t == "func":
        return functions.run(str(app.get("func", "")), app)
    raise ValueError(f"unknown entry type {t!r}")


# --------------------------------------------------------------------------- status page

def render_status(cfg: dict) -> bytes:
    ip = lan_ip()
    token = cfg["token"]
    masked = token[:4] + "…" + token[-2:] if len(token) > 8 else "…"
    n = len(cfg["apps"])
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TriggerPanel</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 640px; margin: 3rem auto;
         padding: 0 1rem; color: #cdd6f4; background: #181825; }}
  h1 {{ font-size: 1.3rem; color: #cdd6f4; }}
  code {{ background: #11111b; color: #cdd6f4; padding: .1rem .35rem;
          border-radius: 4px; font-size: .88em; word-break: break-all;
          border: 1px solid #45475a; }}
  .muted {{ color: #6c7086; font-size: .85rem; }}
  a {{ color: #89b4fa; }}
</style>
</head>
<body>
  <h1>TriggerPanel — running</h1>
  <p>Serving on <b>http://{ip}:{cfg["port"]}</b> (this PC's LAN address).
     Manage entries in the <b>TriggerPanel</b> desktop app
     (system tray &rarr; <i>Show Window</i>).</p>
  <p>Shortcut URL format:<br>
     <code>http://{ip}:{cfg["port"]}/run?app=1&amp;token=…</code><br>
     <span class="muted">Copy ready-made URLs from the desktop app.
     Token: {html.escape(masked)} &middot; {n} entr(y/ies) configured.</span></p>
  <p>Phone panel: <a href="/panel">/panel</a> — an Apple-style page of icon
     buttons for every entry. Open it via the tokenized link returned by
     <code>/list?token=…</code>.</p>
  <p class="muted">Tip: reserve this IP in your router (DHCP reservation)
     so it never changes and your shortcuts keep working.</p>
</body>
</html>""".encode("utf-8")


# The hosted phone-panel page. Deliberately a plain (non-f) string: it is
# full of JS braces. Icons render from the Iconify API (the upstream source
# IconVaultKit indexes — it has no icon-by-id URL of its own); ids are
# browsed at https://iconvaultkit.com/icons. Entries load client-side from
# /list, so no server-side interpolation is needed.
PANEL_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>TriggerPanel</title>
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<meta name="apple-mobile-web-app-title" content="TriggerPanel">
<link rel="apple-touch-icon" href="/icon">
<style>
  :root { --bg:#181825; --tile-a:#313244; --tile-b:#1e1e2e; --text:#cdd6f4;
          --muted:#6c7086; --accent:#89b4fa; --ok:#a6e3a1; --bad:#f38ba8; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body {
    margin:0; min-height:100vh; color:var(--text);
    font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background:
      radial-gradient(1200px 500px at 20% -10%, #1e1e3f 0%, transparent 60%),
      radial-gradient(900px 400px at 90% 0%, #16213a 0%, transparent 55%),
      linear-gradient(180deg, var(--bg), #11111b);
    background-attachment: fixed;
  }
  header { padding:30px 20px 6px; text-align:center; }
  h1 { margin:0; font-size:23px; font-weight:700; letter-spacing:.2px; }
  .sub { color:var(--muted); font-size:12.5px; margin-top:5px; }
  .banner { margin:12px auto 0; max-width:520px; padding:9px 14px;
            border-radius:10px; font-size:12.5px; background:#3a2a3a;
            color:var(--bad); display:none; }
  .grid {
    display:grid; grid-template-columns:repeat(auto-fill, minmax(88px, 1fr));
    gap:24px 12px; padding:28px 22px 40px; max-width:760px; margin:0 auto;
  }
  .tile {
    appearance:none; border:0; background:transparent; cursor:pointer;
    display:flex; flex-direction:column; align-items:center; gap:9px;
    padding:4px 0; font:inherit; color:inherit;
    transition:transform .18s cubic-bezier(.2,.8,.2,1);
  }
  .tile:active { transform:scale(.92); }
  .ico {
    width:62px; height:62px; border-radius:16px;
    background:linear-gradient(160deg, var(--tile-a), var(--tile-b));
    border:1px solid rgba(255,255,255,.08);
    box-shadow:0 8px 18px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.06);
    display:flex; align-items:center; justify-content:center;
    font-size:24px; font-weight:700; color:var(--accent); position:relative;
    transition:box-shadow .18s ease, border-color .18s ease;
  }
  .tile:hover .ico {
    border-color:rgba(137,180,250,.55);
    box-shadow:0 10px 22px rgba(0,0,0,.4), 0 0 0 3px rgba(137,180,250,.18);
  }
  .ico img { width:32px; height:32px; display:block; }
  .lbl {
    font-size:11.5px; line-height:1.25; text-align:center; max-width:86px;
    display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
    overflow:hidden; text-shadow:0 1px 2px rgba(0,0,0,.6);
  }
  .badge {
    position:absolute; inset:0; border-radius:16px; display:none;
    align-items:center; justify-content:center; font-size:26px;
    background:rgba(17,17,27,.78);
  }
  .tile.ok .badge { display:flex; color:var(--ok); }
  .tile.bad .badge { display:flex; color:var(--bad); }
  .tile.busy .ico { animation:pulse .7s ease-in-out infinite alternate; }
  @keyframes pulse { from { opacity:.55 } to { opacity:1 } }
  .toast {
    position:fixed; left:50%; bottom:26px;
    transform:translateX(-50%) translateY(90px);
    background:#313244; border:1px solid #45475a; color:var(--text);
    padding:10px 16px; border-radius:999px; font-size:13px; opacity:0;
    transition:.25s ease; box-shadow:0 10px 24px rgba(0,0,0,.45); z-index:10;
    max-width:86vw; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .toast.show { transform:translateX(-50%) translateY(0); opacity:1; }
  .actions { display:flex; gap:10px; justify-content:center; flex-wrap:wrap;
              padding:4px 20px 0; }
  .chip { appearance:none; cursor:pointer; font:inherit; font-size:12.5px;
          color:var(--text); background:#313244; border:1px solid #45475a;
          border-radius:999px; padding:8px 16px;
          transition:background .15s ease, border-color .15s ease; }
  .chip:hover { background:#45475a; border-color:#6c7086; }
  .openchip { background:#89b4fa; color:#11111b; border:none;
              font-weight:bold; text-decoration:none; display:inline-block; }
  .openchip:hover { background:#b4d3fb; border:none; }
  .modal { position:fixed; inset:0; display:none; align-items:center;
           justify-content:center; background:rgba(0,0,0,.55); z-index:20;
           padding:20px; }
  .modal.show { display:flex; }
  .card { background:#1e1e2e; border:1px solid #6c7086; border-radius:16px;
          box-shadow:0 18px 40px rgba(0,0,0,.5); max-width:430px; width:100%;
          padding:20px 20px 16px; max-height:86vh; overflow:auto; }
  .card h2 { margin:0 0 6px; font-size:16px; }
  .card ol { padding-left:20px; margin:8px 0; }
  .card li, .card p { font-size:13px; line-height:1.55; color:var(--text); }
  .card .muted2 { color:var(--muted); font-size:11.5px; margin:6px 0; }
  .copyrow { display:flex; gap:8px; align-items:center; margin:7px 0;
             font-size:12px; }
  .copyrow .u { flex:1; min-width:0; white-space:nowrap; overflow:hidden;
                text-overflow:ellipsis; background:#181825;
                border:1px solid #45475a; border-radius:8px; padding:7px 9px;
                font-family:ui-monospace, Consolas, monospace; color:#a6adc8; }
  .copyrow .chip { flex:none; padding:7px 14px; }
  .closebtn { width:100%; margin-top:12px; }
  footer { text-align:center; padding:0 20px 36px; color:var(--muted);
           font-size:12px; line-height:1.7; }
  footer a { color:var(--accent); text-decoration:none; }
</style>
</head>
<body>
  <header>
    <h1>TriggerPanel</h1>
    <div class="sub" id="sub">loading…</div>
    <div class="banner" id="banner"></div>
  </header>
  <div class="grid" id="grid"></div>
  <div class="actions">
    <button class="chip" id="btnHome">⌂ Add to Home Screen</button>
    <button class="chip" id="btnShort">⚡ Make an iOS Shortcut</button>
  </div>
  <footer>
    Tap an icon to trigger it on your PC.<br>
    Find icon ids at
    <a href="https://iconvaultkit.com/icons" target="_blank" rel="noopener">iconvaultkit.com/icons</a>
    (e.g. <code>flowbite:bug-solid</code>)
  </footer>

  <div class="modal" id="modalHome">
    <div class="card">
      <h2>Add to Home Screen</h2>
      <ol>
        <li>Open this page in <b>Safari</b> on your iPhone.</li>
        <li>Tap the <b>Share</b> button (square with an arrow).</li>
        <li>Choose <b>Add to Home Screen</b> → <b>Add</b>.</li>
      </ol>
      <p class="muted2">It appears on your home screen with the TriggerPanel
      icon — one tap to the whole button grid.</p>
      <button class="chip closebtn" data-close>Got it</button>
    </div>
  </div>

  <div class="modal" id="modalShort">
    <div class="card">
      <h2>Make an iOS Shortcut</h2>
      <p class="muted2">Apple only lets signed shortcuts install with a single
      tap, so a page can't install one for you — this takes ~30 seconds the
      first time:</p>
      <ol>
        <li>Pick an entry below → <b>Copy</b> its URL.</li>
        <li>Tap <b>Open Shortcut editor</b> — jumps straight into a new
            shortcut in the Shortcuts app.</li>
        <li>Add action <b>Get Contents of URL</b> → paste the URL.</li>
        <li>Optional: add <b>Show Notification</b> with
            <i>Contents of URL</i> so you see the result.</li>
        <li>Next ones: duplicate the shortcut and change
            <code>app=1</code> → <code>app=2</code>, …</li>
      </ol>
      <p style="text-align:center; margin:12px 0 4px;">
        <a class="chip openchip" href="shortcuts://create-shortcut">⚡ Open
        Shortcut editor</a>
      </p>
      <div id="urlList"></div>
      <button class="chip closebtn" data-close>Done</button>
    </div>
  </div>
  <div class="toast" id="toast"></div>
<script>
const token = new URLSearchParams(location.search).get("token") || "";
const grid = document.getElementById("grid");
const sub = document.getElementById("sub");
const banner = document.getElementById("banner");
const toastEl = document.getElementById("toast");
let toastTimer = null;
const copyData = { entries: [] };   // filled from /list, used by the modals

function toast(msg, bad) {
  toastEl.textContent = msg;
  toastEl.style.borderColor = bad ? "#f38ba8" : "#a6e3a1";
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2000);
}

function iconSrc(icon) {
  if (!icon) return null;
  if (/^https?:\/\//.test(icon)) return icon;      // manual image URL
  if (icon.includes(":")) {                        // iconify-style id
    const parts = icon.split(":");
    return "https://api.iconify.design/" + parts[0] + "/" + parts[1]
         + ".svg?color=%2389b4fa";
  }
  return null;
}

function buildTile(e) {
  const btn = document.createElement("button");
  btn.className = "tile";
  const ico = document.createElement("span");
  ico.className = "ico";
  const src = iconSrc(e.icon);
  if (src) {
    const img = document.createElement("img");
    img.src = src;
    img.alt = "";
    img.onerror = function () { if (img.parentNode) img.remove();
                                ico.firstChild
                                  ? ico.replaceChild(
                                        document.createTextNode(String(e.id)),
                                        ico.firstChild)
                                  : ico.append(String(e.id)); };
    ico.appendChild(img);
  } else {
    ico.textContent = String(e.id);
  }
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = "✓";
  ico.appendChild(badge);
  const lbl = document.createElement("span");
  lbl.className = "lbl";
  lbl.textContent = e.name || ("Entry " + e.id);
  btn.appendChild(ico);
  btn.appendChild(lbl);
  btn.addEventListener("click", async function () {
    if (btn.classList.contains("busy")) return;
    btn.classList.remove("ok", "bad");
    btn.classList.add("busy");
    try {
      const r = await fetch(e.run);
      const j = await r.json();
      btn.classList.remove("busy");
      if (j.ok) {
        btn.classList.add("ok");
        badge.textContent = "✓";
        toast("✓ " + (e.name || e.id) + (j.detail ? " — " + j.detail : ""));
      } else {
        btn.classList.add("bad");
        badge.textContent = "✗";
        toast(j.error || "failed", true);
      }
    } catch (err) {
      btn.classList.remove("busy");
      btn.classList.add("bad");
      badge.textContent = "✗";
      toast("PC unreachable", true);
    }
    setTimeout(function () { btn.classList.remove("ok", "bad"); }, 1700);
  });
  return btn;
}

(async function init() {
  if (!token) {
    banner.textContent = "Missing token — open the link returned by "
                       + "/list?token=… (Copy URL in the desktop app).";
    banner.style.display = "block";
    sub.textContent = "no token";
    return;
  }
  try {
    const r = await fetch("/list?token=" + encodeURIComponent(token));
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || "failed");
    sub.textContent = j.entries.length + " entr"
      + (j.entries.length === 1 ? "y" : "ies") + " · " + location.host;
    grid.innerHTML = "";
    copyData.entries = j.entries;
    j.entries.forEach(function (e) { grid.appendChild(buildTile(e)); });
    if (!j.entries.length) {
      sub.textContent = "no entries yet — add some in the desktop app";
    }
  } catch (err) {
    banner.textContent = (err.message === "bad token")
      ? "Bad token — copy a fresh URL from the desktop app."
      : "Could not load /list — is TriggerPanel running?";
    banner.style.display = "block";
    sub.textContent = "error";
  }
})();

// ---- Add-to-Home / Shortcut-guide modals ----
function copyText(t) {
  const ta = document.createElement("textarea");
  ta.value = t;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) {}
  ta.remove();
  if (!ok && navigator.clipboard) {
    try { navigator.clipboard.writeText(t); ok = true; } catch (e) {}
  }
  return ok;
}

function openModal(id) {
  if (id === "modalShort") {
    const list = document.getElementById("urlList");
    list.innerHTML = "";
    if (!copyData.entries.length) {
      list.innerHTML = '<p class="muted2">No entries yet — add some in the desktop app.</p>';
    }
    copyData.entries.forEach(function (e) {
      const row = document.createElement("div");
      row.className = "copyrow";
      const u = document.createElement("span");
      u.className = "u";
      u.textContent = "#" + e.id + "  " + e.run;
      const b = document.createElement("button");
      b.className = "chip";
      b.textContent = "Copy";
      b.onclick = function () {
        const did = copyText(e.run);
        toast(did ? "Copied #" + e.id + " URL ✓" : "Copy failed", !did);
      };
      row.appendChild(u);
      row.appendChild(b);
      list.appendChild(row);
    });
  }
  document.getElementById(id).classList.add("show");
}

document.getElementById("btnHome").onclick = function () {
  openModal("modalHome");
};
document.getElementById("btnShort").onclick = function () {
  openModal("modalShort");
};
document.querySelectorAll("[data-close]").forEach(function (b) {
  b.onclick = function () {
    const m = b.closest(".modal");
    if (m) m.classList.remove("show");
  };
});
document.querySelectorAll(".modal").forEach(function (m) {
  m.addEventListener("click", function (ev) {
    if (ev.target === m) m.classList.remove("show");
  });
});
</script>
</body>
</html>
""".encode("utf-8")


# --------------------------------------------------------------------------- http server

class Handler(BaseHTTPRequestHandler):
    server_version = "TriggerPanel/4.0"

    def log_message(self, fmt, *args):  # route access log into server.log
        log(f"{self.client_address[0]}  {fmt % args}")

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path == "/":
            self._send(200, render_status(load_config()),
                       "text/html; charset=utf-8")
        elif u.path == "/health":
            self._send(200, b"ok", "text/plain")
        elif u.path == "/run":
            self._handle_run(q)
        elif u.path == "/list":
            self._handle_list(q)
        elif u.path == "/panel":
            self._send(200, PANEL_HTML, "text/html; charset=utf-8")
        elif u.path == "/icon":
            if ICON_PNG:
                self._send(200, ICON_PNG, "image/png")
            else:
                self._json(404, {"ok": False, "error": "icon not rendered"})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # management moved to the desktop GUI
        self._json(410, {"ok": False, "error": "manage entries in the desktop app"})

    def _handle_run(self, q: dict) -> None:
        cfg = load_config()
        token = (q.get("token") or [""])[0]
        if not secrets.compare_digest(token.encode("utf-8"),
                                      cfg["token"].encode("utf-8")):
            log("REJECTED /run — bad token")
            self._json(401, {"ok": False, "error": "bad token"})
            return

        raw_id = (q.get("app") or [""])[0]
        try:
            app_id = int(raw_id)
        except ValueError:
            self._json(400, {"ok": False, "error": "app must be a number"})
            return

        app = find_app(cfg, app_id)
        if app is None:
            self._json(404, {"ok": False, "error": f"no entry with index {app_id}"})
            return

        try:
            detail = execute(app)
        except Exception as e:  # surface to the phone so Shortcuts can show it
            log(f"FAILED #{app_id} {app.get('name')!r} [{entry_type(app)}]: {e}")
            self._json(500, {"ok": False, "error": str(e)})
            return

        log(f"ran #{app_id} {app.get('name')!r} [{entry_type(app)}] "
            f"— {detail} — for {self.client_address[0]}")
        self._json(200, {"ok": True, "app": app_id, "name": app.get("name"),
                         "type": entry_type(app), "detail": detail})

    def _handle_list(self, q: dict) -> None:
        """GET /list?token=… → the panel page link + every entry's run URL."""
        cfg = load_config()
        token = (q.get("token") or [""])[0]
        if not secrets.compare_digest(token.encode("utf-8"),
                                      cfg["token"].encode("utf-8")):
            log("REJECTED /list — bad token")
            self._json(401, {"ok": False, "error": "bad token"})
            return
        ip = lan_ip()
        entries = [{
            "id": a["id"],
            "name": a.get("name", ""),
            "type": entry_type(a),
            "icon": a.get("icon", ""),
            "details": entry_details(a),
            "run": run_url(cfg, a["id"]),
        } for a in cfg["apps"]]
        self._json(200, {
            "ok": True,
            "page": f"http://{ip}:{cfg['port']}/panel?token={token}",
            "entries": entries,
        })


# --------------------------------------------------------------------------- gui: main window

class TitleBar(QWidget):
    """TradingBot-style chrome: 32px #11111b bar, drag to move."""

    def __init__(self, win: "TriggerWindow"):
        super().__init__(win)
        self._win = win
        self.setFixedHeight(32)
        self._drag_pos = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 2, 0)
        lay.setSpacing(0)

        title = QLabel(f"TriggerPanel  v{APP_VERSION}")
        title.setObjectName("appTitle")
        title.setStyleSheet(
            f"color: {TEXT}; font-size: 12px; font-weight: bold; "
            "background: transparent;")
        lay.addWidget(title)

        lay.addStretch(1)

        gear = QPushButton()  # settings — icon only, no text
        gear.setObjectName("settingsBtn")
        gear.setFixedSize(42, 32)
        gear.setCursor(Qt.CursorShape.PointingHandCursor)
        gear.setToolTip("Settings")
        gear_pm = QPixmap(os.path.join(BASE_DIR, SETTINGS_ICON))
        if not gear_pm.isNull():
            gear.setIcon(QIcon(gear_pm))
            gear.setIconSize(QSize(16, 16))
        else:
            gear.setText("⚙")  # fallback when the icon file is missing
        gear.clicked.connect(win.open_settings)
        lay.addWidget(gear)

        min_btn = QPushButton("—")
        min_btn.setObjectName("titleBtn")
        min_btn.setFixedSize(42, 32)
        min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        min_btn.clicked.connect(win.to_tray)
        lay.addWidget(min_btn)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("titleClose")
        close_btn.setFixedSize(42, 32)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(win.close_clicked)
        lay.addWidget(close_btn)

    # Manual window drag — TradingBot's proven approach (startSystemMove()
    # is unreliable from child widgets in PyQt6 on Windows).
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.window().pos()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event) -> None:
        if self.window().isMaximized():
            self.window().showNormal()
        else:
            self.window().showMaximized()


# (token field removed from the main window — the token lives in Settings)


_TYPE_COLORS = {"app": BLUE, "keys": GREEN, "http": YELLOW, "func": PINK}


class TriggerWindow(QWidget):
    def __init__(self, icon: QIcon, enable_tray: bool = True):
        super().__init__()
        self._icon = icon
        self._quitting = False
        self._wiz: NewEntryOverlay | None = None
        cfg = load_config()
        self._port = int(cfg["port"])

        self.setWindowTitle(f"TriggerPanel v{APP_VERSION}")
        self.setWindowIcon(icon)
        self.setMinimumSize(560, 400)
        self.resize(760, 480)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(TitleBar(self))

        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(10)
        outer.addWidget(content, 1)

        # ── status + panel link row ───────────────────────────────────
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self._status = QLabel("●  Running")
        self._status.setObjectName("statusOk")
        self._status_ok_text = self._status.text()
        status_row.addWidget(self._status)
        self._panel_link = QLabel()
        self._panel_link.setObjectName("panelLink")
        self._panel_link.setOpenExternalLinks(True)  # click → default browser
        self._refresh_panel_link()
        status_row.addWidget(self._panel_link, 1)
        panel_copy = QPushButton("Copy")
        panel_copy.setToolTip("Copy the panel link (with token) to share")
        panel_copy.clicked.connect(
            lambda: self._copy(self._panel_url(), panel_copy))
        status_row.addWidget(panel_copy)
        hint = QLabel("reserve this IP in your router so it never changes")
        hint.setObjectName("muted")
        status_row.addWidget(hint)
        v.addLayout(status_row)

        # ── entry list ────────────────────────────────────────────────
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["#", "Name", "Type", "Details"])
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0, 52)
        self.tree.setColumnWidth(2, 60)
        self.tree.itemDoubleClicked.connect(lambda *_: self.run_selected())
        v.addWidget(self.tree, 1)

        # ── action buttons ────────────────────────────────────────────
        btns = QHBoxLayout()
        btns.setSpacing(6)
        run_btn = QPushButton("Run")
        run_btn.clicked.connect(self.run_selected)
        btns.addWidget(run_btn)
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self.edit_selected)
        btns.addWidget(edit_btn)
        copy_url_btn = QPushButton("Copy URL")
        copy_url_btn.clicked.connect(lambda: self.copy_url(copy_url_btn))
        btns.addWidget(copy_url_btn)
        del_btn = QPushButton("Delete")
        del_btn.setObjectName("danger")
        del_btn.clicked.connect(self.delete_selected)
        btns.addWidget(del_btn)
        btns.addStretch(1)
        new_btn = QPushButton("+ New entry")
        new_btn.setObjectName("accent")
        new_btn.clicked.connect(self.open_wizard)
        btns.addWidget(new_btn)
        quit_btn = QPushButton("Quit")
        quit_btn.clicked.connect(self.quit_app)
        btns.addWidget(quit_btn)
        v.addLayout(btns)

        # ── system tray ───────────────────────────────────────────────
        self._tray_ok = False
        self._tray = None
        if enable_tray:
            tray = QSystemTrayIcon(icon, self)
            tray.setToolTip(f"TriggerPanel — http://{lan_ip()}:{self._port}")
            menu = QMenu(self)
            show_action = menu.addAction("Show Window")
            show_action.triggered.connect(self.restore_from_tray)
            menu.addSeparator()
            exit_action = menu.addAction("Exit")
            exit_action.triggered.connect(self.quit_app)
            tray.setContextMenu(menu)
            tray.activated.connect(self._tray_activated)
            tray.show()
            self._tray = tray
            self._tray_ok = tray.isSystemTrayAvailable()

        self.refresh()

    # -- window / tray behavior ------------------------------------------

    def to_tray(self) -> None:
        if self._tray_ok:
            self.hide()
        else:
            self.showMinimized()

    def close_clicked(self) -> None:
        if self._tray_ok:
            self.hide()
        else:
            self.quit_app()

    def restore_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.restore_from_tray()

    def changeEvent(self, e) -> None:
        if (e.type() == QEvent.Type.WindowStateChange
                and self.isMinimized() and self._tray_ok):
            QTimer.singleShot(0, self.hide)
        super().changeEvent(e)

    def closeEvent(self, e) -> None:
        if self._quitting:
            e.accept()
            return
        if self._tray_ok:
            e.ignore()
            self.hide()
            return
        if not self._confirm_quit():
            e.ignore()
            return
        self._mark_quitting()
        e.accept()

    def quit_app(self) -> None:
        if self._quitting:
            return
        if not self._confirm_quit():
            return
        self._mark_quitting()
        self.close()  # closeEvent sees _quitting → accepts

    def _confirm_quit(self) -> bool:
        return QMessageBox.question(
            self, "TriggerPanel",
            "Exit TriggerPanel?\n\nThe server will stop — your phone "
            "shortcuts won't work until you start it again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _mark_quitting(self) -> None:
        self._quitting = True
        if self._tray is not None:
            self._tray.hide()

    # -- list + actions ---------------------------------------------------

    def refresh(self, select_id: int | None = None) -> None:
        self.tree.clear()
        select_item = None
        for app in load_config()["apps"]:
            t = entry_type(app)
            item = QTreeWidgetItem(
                [f"#{app['id']}", app.get("name", ""), t, entry_details(app)])
            item.setData(0, Qt.ItemDataRole.UserRole, app["id"])
            item.setForeground(2, QBrush(QColor(_TYPE_COLORS.get(t, TEXT))))
            self.tree.addTopLevelItem(item)
            if select_id is not None and app["id"] == select_id:
                select_item = item
        if select_item is not None:
            self.tree.setCurrentItem(select_item)

    def _selected(self, announce: bool = True) -> int | None:
        item = self.tree.currentItem()
        if item is None:
            if announce:
                QMessageBox.information(self, "TriggerPanel",
                                        "Select an entry first.")
            return None
        return int(item.data(0, Qt.ItemDataRole.UserRole))

    # -- wizard -----------------------------------------------------------

    def open_wizard(self) -> None:
        self._open_wizard(None)

    def edit_selected(self) -> None:
        app_id = self._selected()
        if app_id is None:
            return
        app = find_app(load_config(), app_id)
        if app is None:
            self.refresh()
            return
        self._open_wizard(app)

    def _open_wizard(self, entry: dict | None) -> None:
        if self._wiz is not None:
            self._wiz.close()
        self._wiz = NewEntryOverlay(self, on_added=self._entry_added,
                                    entry=entry)
        self._wiz.show_centered()

    def _entry_added(self, new_id: int, name: str,
                     is_edit: bool = False) -> None:
        self.refresh(select_id=new_id)
        if is_edit:
            self._flash_status(f"●  Saved #{new_id} — {name}")
        else:
            self._flash_status(f"●  Added {name} — it's #{new_id}")

    # -- settings -----------------------------------------------------------

    def open_settings(self) -> None:
        if self._wiz is not None:
            self._wiz.close()
        self._settings = SettingsOverlay(self, on_saved=self._settings_saved)
        self._settings.show_centered()

    def _settings_saved(self, port_changed: bool, cfg: dict) -> None:
        """Refresh the chrome after Settings wrote config.json."""
        if port_changed:
            self._port = int(cfg["port"])
        self._refresh_panel_link()  # token and/or port may have changed
        if port_changed:
            answer = QMessageBox.question(
                self, "TriggerPanel",
                "Settings saved.\n\nThe port changed — restart TriggerPanel "
                "now? (Token and Run-at-boot already apply.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                self._request_restart()
                return
            self._flash_status("●  Settings saved — restart to use the new port")
        else:
            self._flash_status("●  Settings saved")

    def _request_restart(self) -> None:
        """Quit and let main() spawn a fresh process (new port binds cleanly)."""
        _RESTART["pending"] = True
        self._mark_quitting()
        self.close()

    # -- run / delete -----------------------------------------------------

    def run_selected(self) -> None:
        app_id = self._selected()
        if app_id is None:
            return
        app = find_app(load_config(), app_id)
        if app is None:
            self.refresh()
            return
        try:
            detail = execute(app)
        except Exception as e:
            log(f"FAILED #{app_id} {app.get('name')!r} [{entry_type(app)}]: {e}")
            QMessageBox.critical(
                self, "TriggerPanel",
                f"Could not run {app.get('name')}:\n\n{e}")
            return
        log(f"ran #{app_id} {app.get('name')!r} [{entry_type(app)}] — {detail} (GUI)")
        shown = detail if len(detail) <= 70 else detail[:67] + "…"
        self._flash_status(f"●  {shown}")

    def delete_selected(self) -> None:
        app_id = self._selected()
        if app_id is None:
            return
        app = find_app(load_config(), app_id)
        if app is None:
            self.refresh()
            return
        answer = QMessageBox.question(
            self, "TriggerPanel",
            f"Delete #{app_id} — {app.get('name')}?\n\n"
            "Other entries keep their numbers.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        with CONFIG_LOCK:
            cfg = load_config()
            cfg["apps"] = [a for a in cfg["apps"] if a["id"] != app_id]
            _write_config(cfg)
        log(f"deleted #{app_id} {app.get('name')!r}")
        self.refresh()

    def _panel_url(self) -> str:
        """Shareable panel link — includes the token so tiles can fire."""
        cfg = load_config()
        return (f"http://{lan_ip()}:{cfg['port']}/panel"
                f"?token={cfg['token']}")

    def _refresh_panel_link(self) -> None:
        url = self._panel_url()
        # Show only the clean panel URL — the token stays hidden inside the
        # anchor's href, so clicking still opens the working link and Copy
        # (via _panel_url) still shares the full tokenized URL.
        display = url.split("?")[0]
        self._panel_link.setText(
            f'<a href="{url}" style="color:{BLUE}; text-decoration:none;">'
            f"{display}</a>")

    def copy_url(self, btn: QPushButton | None = None) -> None:
        app_id = self._selected()
        if app_id is None:
            return
        self._copy(run_url(load_config(), app_id), btn)

    def _copy(self, text: str, btn: QPushButton | None = None) -> None:
        QApplication.clipboard().setText(text)
        if btn is not None:
            self._flash_btn(btn)

    def _flash_btn(self, btn: QPushButton, text: str = "Copied!") -> None:
        try:
            original = btn.text()
            btn.setText(text)
        except RuntimeError:
            return
        QTimer.singleShot(1200, lambda: self._restore_btn(btn, original))

    @staticmethod
    def _restore_btn(btn: QPushButton, text: str) -> None:
        try:
            btn.setText(text)
        except RuntimeError:
            pass  # widget already gone

    def _flash_status(self, text: str, ok: bool = False) -> None:
        try:
            self._status.setText(text)
            self._status.setObjectName("statusOk" if ok else "statusInfo")
            self._status.style().unpolish(self._status)
            self._status.style().polish(self._status)
        except RuntimeError:
            return
        QTimer.singleShot(2500, self._restore_status)

    def _restore_status(self) -> None:
        try:
            self._status.setText(self._status_ok_text)
            self._status.setObjectName("statusOk")
            self._status.style().unpolish(self._status)
            self._status.style().polish(self._status)
        except RuntimeError:
            pass

    # -- frameless resize (WM_NCHITTEST — TradingBot's handler) -----------

    def nativeEvent(self, eventType, message):
        """Resize borders for the frameless window (TradingBot's WM_NCHITTEST).

        8px borders on all edges + corners get native resize cursors and
        drag-resize; disabled while maximized/minimized.
        """
        if eventType == b"windows_generic_MSG":
            try:
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0084 and not (self.isMaximized()
                                                  or self.isMinimized()):
                    # WM_NCHITTEST — lParam is physical screen px. GetWindowRect
                    # returns the same units on every monitor/DPI; Qt's logical
                    # geometry does not, and mis-matching turns the whole client
                    # area into a resize zone on secondary monitors.
                    x = ctypes.c_short(msg.lParam & 0xFFFF).value
                    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    rect = ctypes.wintypes.RECT()
                    if not ctypes.windll.user32.GetWindowRect(
                            int(self.winId()), ctypes.byref(rect)):
                        return False, 0
                    b = 8  # border width, physical px
                    on_left = rect.left <= x < rect.left + b
                    on_right = rect.right - b <= x < rect.right
                    on_top = rect.top <= y < rect.top + b
                    on_bottom = rect.bottom - b <= y < rect.bottom
                    if on_left and on_top:     return True, 13  # HTTOPLEFT
                    if on_right and on_top:    return True, 14  # HTTOPRIGHT
                    if on_left and on_bottom:  return True, 16  # HTBOTTOMLEFT
                    if on_right and on_bottom: return True, 17  # HTBOTTOMRIGHT
                    if on_left:   return True, 10  # HTLEFT
                    if on_right:  return True, 11  # HTRIGHT
                    if on_top:    return True, 12  # HTTOP
                    if on_bottom: return True, 15  # HTBOTTOM
            except Exception:
                pass
        # Never call super().nativeEvent — it access-violates inside PyQt6
        # during window show. QWidget's base implementation only returns
        # False anyway, which is exactly the unhandled result Qt expects.
        return False, 0


# --------------------------------------------------------------------------- gui: wizard overlay

def _apply_overlay_chrome(widget: QWidget, blur: int = 24,
                          offset_y: int = 4, alpha: int = 170) -> None:
    """TradingBot's overlay chrome: soft drop shadow behind the light border."""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, offset_y)
    shadow.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(shadow)


class NewEntryOverlay(QWidget):
    """Frameless 2-step wizard for creating an entry — TradingBot overlay style.

    NOT Qt.WindowType.Popup: against an active parent window the OS unmaps
    popups instantly. Explicit dismissal instead — Escape, outside press,
    or ✕ (same pattern as TradingBot's api_limits_popup).
    """

    def __init__(self, win: TriggerWindow, on_added=None,
                 entry: dict | None = None) -> None:
        super().__init__(win, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Tool)
        self._win = win
        self._on_added = on_added      # callable(entry_id, name, is_edit)
        self._chosen: str | None = None
        self._edit_id = entry["id"] if entry is not None else None

        self.setWindowTitle("Edit entry" if entry else "New entry")
        # 5 type cards on page 1 + the tallest details page — keep both inside.
        self.setFixedSize(500, 520)
        self.setStyleSheet(WIZARD_STYLESHEET)
        _apply_overlay_chrome(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 14)
        root.setSpacing(8)

        # ── title row ─────────────────────────────────────────────────
        title_row = QHBoxLayout()
        title = QLabel("Edit entry" if entry else "New entry")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        title_row.addWidget(title)
        self._step_lbl = QLabel("Step 1 of 2 — pick an action type")
        self._step_lbl.setObjectName("stepLbl")
        title_row.addStretch(1)
        title_row.addWidget(self._step_lbl)
        close_x = QPushButton("✕")
        close_x.setObjectName("titleClose")
        close_x.setFixedSize(26, 26)
        close_x.setCursor(Qt.CursorShape.PointingHandCursor)
        close_x.clicked.connect(self.close)
        title_row.addWidget(close_x)
        root.addLayout(title_row)

        # ── pages ─────────────────────────────────────────────────────
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)
        self._stack.addWidget(self._page_pick())
        self._stack.addWidget(self._page_details())
        self._stack.setCurrentIndex(0)

        # ── footer ────────────────────────────────────────────────────
        foot = QHBoxLayout()
        foot.setSpacing(6)
        self._back_btn = QPushButton("← Back")
        self._back_btn.clicked.connect(self._go_pick)
        self._back_btn.hide()
        foot.addWidget(self._back_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.close)
        foot.addWidget(cancel_btn)
        foot.addStretch(1)
        self._add_btn = QPushButton("Add entry")
        self._add_btn.setObjectName("accent")
        self._add_btn.clicked.connect(self._add)
        self._add_btn.hide()
        foot.addWidget(self._add_btn)
        root.addLayout(foot)

        if entry is not None:
            # Edit mode: jump straight to details with everything prefilled.
            self._add_btn.setText("Save")
            t = entry_type(entry)
            self._choose(t)             # sets type + clears fields…
            self._name_edit.setText(entry.get("name", ""))  # …then prefill
            self._icon_edit.setText(entry.get("icon", ""))
            if t == "app":
                self._path_edit.setText(entry.get("path", ""))
            elif t == "script":
                self._script_edit.setText(entry.get("script", ""))
                self._venv_edit.setText(entry.get("venv", ""))
            elif t == "keys":
                self._keys_edit.setText(entry.get("keys", ""))
            elif t == "http":
                self._url_edit.setText(entry.get("url", ""))
                self._method_box.setCurrentText(entry.get("method", "GET"))
                self._body_edit.setText(entry.get("body", ""))
            elif t == "func":
                key = str(entry.get("func", ""))
                fn = functions.FUNCTIONS.get(key)
                self._func_box.setCurrentText(
                    fn["label"] if fn else next(iter(self._func_labels), ""))
                spec = str(entry.get("monitors", "all")).strip().lower()
                want = None if spec in ("", "all") else {
                    int(s) for s in spec.split(",") if s.strip().isdigit()}
                for i, cb in self._mon_selected:
                    cb.setChecked(want is None or i in want)
                try:
                    self._value_spin.setValue(int(entry.get("value", 50)))
                except (TypeError, ValueError):
                    pass
                self._fade_check.setChecked(bool(entry.get("fade", True)))
                speed = str(entry.get("speed", functions.DEFAULT_SPEED))
                if speed in functions.FADE_SPEEDS:
                    self._speed_box.setCurrentText(speed)
            self._step_lbl.setText(f"Editing #{entry['id']} — {t}")
            self._name_edit.selectAll()

    # -- page 1: pick a type ---------------------------------------------

    def _page_pick(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")  # opaque background — page 2 must not bleed through
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(8)
        intro = QLabel("What should this entry do when triggered?")
        intro.setStyleSheet(f"font-size: 13px; color: {SUBTEXT};")
        lay.addWidget(intro)

        cards = [
            ("▶  Run an app",
             "Launch an .exe, file, URI or command line",
             "app"),
            ("🐍  Python script",
             "Run a .py file with its own virtualenv",
             "script"),
            ("⌨  Send keystroke(s)",
             "Press a combo in the focused window",
             "keys"),
            ("🌐  Call an API endpoint",
             "Make an HTTP request to a URL",
             "http"),
            ("ƒ  Run Function",
             "Run a built-in action (monitor brightness)",
             "func"),
        ]
        for label, sub, key in cards:
            # Plain text only — rich-text markup rendered as literal tags.
            btn = QPushButton(f"{label}\n{sub}")
            btn.setObjectName("typeCard")
            btn.setFixedHeight(62)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._choose(k))
            lay.addWidget(btn)
        lay.addStretch(1)
        hint = QLabel("Entries get a permanent # — use it as app=<#> in your "
                      "shortcut URL. You can add as many as you like.")
        hint.setObjectName("wizardHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return page

    def _choose(self, key: str) -> None:
        self._chosen = key
        self._name_edit.clear()
        self._path_edit.clear()
        self._script_edit.clear()
        self._venv_edit.clear()
        self._keys_edit.clear()
        self._url_edit.clear()
        self._method_box.setCurrentText("GET")
        self._body_edit.clear()
        self._icon_edit.clear()
        self._func_box.setCurrentIndex(0)
        self._value_spin.setValue(50)
        self._fade_check.setChecked(True)
        self._speed_box.setCurrentText(functions.DEFAULT_SPEED)
        self._icon_find_edit.clear()
        self._icon_hint.hide()
        for cb in self._mon_checks:
            cb.hide()
        self._mon_hint.hide()
        for btn in self._icon_choices:
            btn.hide()
        self._mon_selected = []
        self._detail_stack.setCurrentIndex(ENTRY_TYPES.index(key))
        if key == "func":
            self._fill_monitors()
            key0 = self._func_labels.get(self._func_box.currentText())
            icon = functions.FUNCTIONS.get(key0 or "", {}).get("icon", "")
            if icon:
                self._icon_edit.setText(icon)
        self._step_lbl.setText(f"Step 2 of 2 — {key} details")
        self._back_btn.show()
        self._add_btn.show()
        self._stack.setCurrentIndex(1)
        self._name_edit.setFocus()

    def _go_pick(self) -> None:
        self._step_lbl.setText("Step 1 of 2 — pick an action type")
        self._back_btn.hide()
        self._add_btn.hide()
        self._stack.setCurrentIndex(0)

    # -- live options: displays on this machine, icon suggestions ----------

    def _fill_monitors(self) -> None:
        """Show one checkbox per display this machine reports, all checked."""
        try:
            found = functions.monitors()
        except Exception:
            found = []
        self._mon_selected = []
        if not found:
            self._mon_hint.setText("no controllable display found on this "
                                   "machine — the entry will report that when "
                                   "it runs")
            self._mon_hint.show()
            return
        for cb, m in zip(self._mon_checks, found):
            cb.setText(functions.label(m))
            cb.setChecked(True)
            cb.show()
            self._mon_selected.append((m["index"], cb))

    def _find_icons(self) -> None:
        words = (self._icon_find_edit.text().strip()
                 or self._name_edit.text().strip())
        ids = icon_search(words)
        if not ids:
            self._icon_hint.setText("no matches — offline, or try other words")
            self._icon_hint.show()
            return
        self._icon_hint.hide()
        for btn, icon_id in zip(self._icon_choices, ids):
            btn.setText(icon_id)
            btn.show()
        for btn in self._icon_choices[len(ids):]:
            btn.hide()

    def _icon_take(self, value: str) -> None:
        self._icon_edit.setText(value)
        self._icon_edit.setFocus()

    def _func_fields(self) -> dict:
        """The Run Function options as they stand right now. Raises ValueError
        (the message has already been shown) when they aren't usable."""
        key = self._func_labels.get(self._func_box.currentText())
        if not key:
            QMessageBox.warning(self, "TriggerPanel",
                                "Pick one of the built-in functions.")
            self._func_box.setFocus()
            raise ValueError("no function picked")
        if not self._mon_selected:
            QMessageBox.warning(self, "TriggerPanel",
                                "This machine reports no controllable display.")
            raise ValueError("no displays")
        sel = [i for i, cb in self._mon_selected if cb.isChecked()]
        if not sel:
            QMessageBox.warning(self, "TriggerPanel",
                                "Check at least one display.")
            raise ValueError("no display selected")
        return {
            "func": key,
            "monitors": ("all" if len(sel) == len(self._mon_selected)
                         else ",".join(str(i) for i in sel)),
            "value": self._value_spin.value(),
            "fade": self._fade_check.isChecked(),
            "speed": self._speed_box.currentText(),
        }

    def _test_function(self) -> None:
        """Run the built-in with the options as set — nothing is saved."""
        try:
            fields = self._func_fields()
        except ValueError:
            return
        try:
            detail = functions.run(fields["func"], fields)
        except Exception as exc:
            self._test_result.setText("✗ " + str(exc))
            self._test_result.setStyleSheet(f"color: {RED}; font-size: 12px;")
            return
        self._test_result.setText("✓ " + detail)
        self._test_result.setStyleSheet(f"color: {GREEN}; font-size: 12px;")

    # -- page 2: details --------------------------------------------------

    def _page_details(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")  # opaque background — was transparent
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(8)

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name_row.addWidget(QLabel("Name"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. Volume up")
        self._name_edit.returnPressed.connect(self._add)
        name_row.addWidget(self._name_edit, 1)
        lay.addLayout(name_row)

        icon_row = QHBoxLayout()
        icon_row.setSpacing(6)
        icon_row.addWidget(QLabel("Icon"))
        self._icon_edit = QLineEdit()
        self._icon_edit.setPlaceholderText(
            "flowbite:bug-solid   —   or an image URL (optional)")
        icon_row.addWidget(self._icon_edit, 1)
        lay.addLayout(icon_row)

        # icon picker: search words → real Iconify ids, one tap to take one
        find_row = QHBoxLayout()
        find_row.setSpacing(6)
        find_row.addWidget(QLabel("Find icons"))
        self._icon_find_edit = QLineEdit()
        self._icon_find_edit.setPlaceholderText(
            "words — blank uses the entry name")
        find_row.addWidget(self._icon_find_edit, 1)
        self._icon_find_btn = QPushButton("Find")
        self._icon_find_btn.setObjectName("accent")
        self._icon_find_btn.clicked.connect(self._find_icons)
        find_row.addWidget(self._icon_find_btn)
        lay.addLayout(find_row)

        results_row = QHBoxLayout()
        results_row.setSpacing(6)
        self._icon_hint = QLabel("")
        self._icon_hint.setObjectName("wizardHint")
        self._icon_hint.hide()
        results_row.addWidget(self._icon_hint)
        self._icon_choices = []
        for _ in range(5):
            btn = QPushButton()
            btn.hide()
            btn.clicked.connect(
                lambda _=False, b=btn: self._icon_take(b.text()))
            results_row.addWidget(btn)
            self._icon_choices.append(btn)
        lay.addLayout(results_row)

        # type-specific fields, swapped by the chosen type
        self._detail_stack = QStackedWidget()

        # app
        app_g = QGroupBox(" App ")
        app_l = QVBoxLayout(app_g)
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText(
            r"D:\Apps\cool.exe   or   spotify:   or   a command line")
        self._path_edit.returnPressed.connect(self._add)
        app_l.addWidget(self._path_edit)
        app_hint = QLabel("Full path, URI, or a command line with arguments.")
        app_hint.setObjectName("wizardHint")
        app_l.addWidget(app_hint)
        self._detail_stack.addWidget(app_g)

        # script
        script_g = QGroupBox(" Python script ")
        script_l = QVBoxLayout(script_g)
        self._script_edit = QLineEdit()
        self._script_edit.setPlaceholderText(r"D:\Scripts\my_tool\main.py")
        self._script_edit.returnPressed.connect(self._add)
        script_l.addWidget(self._script_edit)
        venv_row = QHBoxLayout()
        venv_row.setSpacing(6)
        venv_row.addWidget(QLabel("Venv"))
        self._venv_edit = QLineEdit()
        self._venv_edit.setPlaceholderText(
            "auto-detect venv/ or .venv/ next to the script — or set a path")
        self._venv_edit.returnPressed.connect(self._add)
        venv_row.addWidget(self._venv_edit, 1)
        script_l.addLayout(venv_row)
        script_hint = QLabel(
            "Runs with the venv's python.exe; the script's folder becomes the "
            "working directory.")
        script_hint.setObjectName("wizardHint")
        script_hint.setWordWrap(True)
        script_l.addWidget(script_hint)
        self._detail_stack.addWidget(script_g)

        # keys
        keys_g = QGroupBox(" Keystrokes ")
        keys_l = QVBoxLayout(keys_g)
        self._keys_edit = QLineEdit()
        self._keys_edit.setPlaceholderText("ctrl+shift+s, enter")
        self._keys_edit.returnPressed.connect(self._add)
        keys_l.addWidget(self._keys_edit)
        keys_hint = QLabel(
            "Combine keys with <b>+</b>, separate timed steps with <b>,</b>.\n"
            "Keys go to whatever window is focused on your PC when it fires.\n"
            "Named keys: enter, tab, esc, space, up/down/left/right, home, end, "
            "pageup, pagedown, f1–f24, ctrl, shift, alt, win, plus…")
        keys_hint.setObjectName("wizardHint")
        keys_hint.setWordWrap(True)
        keys_l.addWidget(keys_hint)
        self._detail_stack.addWidget(keys_g)

        # http
        http_g = QGroupBox(" API request ")
        http_l = QVBoxLayout(http_g)
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("http://192.168.0.10:8080/api/trigger")
        self._url_edit.returnPressed.connect(self._add)
        http_l.addWidget(self._url_edit)
        method_row = QHBoxLayout()
        method_row.setSpacing(6)
        method_row.addWidget(QLabel("Method"))
        self._method_box = QComboBox()
        self._method_box.addItems(["GET", "POST", "PUT", "PATCH", "DELETE"])
        self._method_box.setMinimumWidth(90)
        method_row.addWidget(self._method_box)
        method_row.addWidget(QLabel("Body"))
        self._body_edit = QLineEdit()
        self._body_edit.setPlaceholderText("optional — for POST/PUT/PATCH")
        method_row.addWidget(self._body_edit, 1)
        http_l.addLayout(method_row)
        http_hint = QLabel("4xx/5xx still counts as “called” — only "
                           "connection failures report an error.")
        http_hint.setObjectName("wizardHint")
        http_hint.setWordWrap(True)
        http_l.addWidget(http_hint)
        self._detail_stack.addWidget(http_g)

        # func — built-in actions (displays are discovered at run time)
        func_g = QGroupBox(" Run Function ")
        func_l = QVBoxLayout(func_g)
        func_row = QHBoxLayout()
        func_row.setSpacing(6)
        func_row.addWidget(QLabel("Function"))
        self._func_box = QComboBox()
        self._func_labels = {fn["label"]: key
                             for key, fn in functions.FUNCTIONS.items()}
        for label in self._func_labels:
            self._func_box.addItem(label)
        self._func_box.setMinimumWidth(150)
        func_row.addWidget(self._func_box, 1)
        func_l.addLayout(func_row)
        # one checkbox per display this machine reports — filled when opened
        self._mon_row = QVBoxLayout()
        func_l.addLayout(self._mon_row)
        self._mon_checks = []
        for _ in range(6):
            cb = QCheckBox()
            cb.hide()
            self._mon_row.addWidget(cb)
            self._mon_checks.append(cb)
        self._mon_hint = QLabel("")
        self._mon_hint.setObjectName("wizardHint")
        self._mon_hint.hide()
        self._mon_row.addWidget(self._mon_hint)
        val_row = QHBoxLayout()
        val_row.setSpacing(6)
        val_row.addWidget(QLabel("Value"))
        self._value_spin = QSpinBox()
        self._value_spin.setRange(0, 100)
        self._value_spin.setValue(50)
        val_row.addWidget(self._value_spin, 1)
        self._fade_check = QCheckBox("Fade")
        self._fade_check.setChecked(True)
        val_row.addWidget(self._fade_check)
        func_l.addLayout(val_row)
        speed_row = QHBoxLayout()
        speed_row.setSpacing(6)
        speed_row.addWidget(QLabel("Fade speed"))
        self._speed_box = QComboBox()
        self._speed_box.addItems(list(functions.FADE_SPEED_ORDER))
        self._speed_box.setCurrentText(functions.DEFAULT_SPEED)
        self._speed_box.setMinimumWidth(90)
        speed_row.addWidget(self._speed_box)
        speed_row.addStretch(1)
        func_l.addLayout(speed_row)
        test_row = QHBoxLayout()
        test_row.setSpacing(6)
        self._test_btn = QPushButton("Test now")
        self._test_btn.clicked.connect(self._test_function)
        test_row.addWidget(self._test_btn)
        self._test_result = QLabel("runs the options above — nothing is saved")
        self._test_result.setObjectName("wizardHint")
        self._test_result.setWordWrap(True)
        test_row.addWidget(self._test_result, 1)
        func_l.addLayout(test_row)
        func_hint = QLabel(
            "Built-ins run inside TriggerPanel — no script path, no venv. "
            "Monitors are found when the entry fires, so nothing here is "
            "tied to this machine.")
        func_hint.setObjectName("wizardHint")
        func_hint.setWordWrap(True)
        func_l.addWidget(func_hint)
        self._detail_stack.addWidget(func_g)

        lay.addWidget(self._detail_stack)
        return page

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Paint the card. A plain QWidget that IS the window root gets no
        background from its own QSS sheet, so the title row was never cleared
        between frames and any label there overpainted its own old text
        (the 'Step 1 of 2' ghost). Filling the rect is what erases it —
        same colours the QSS declares, so nothing can drift."""
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(MANTLE))
        p.setPen(QPen(QColor(OVERLAY), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    # -- dismissal (TradingBot overlay pattern) ---------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().showEvent(e)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def hideEvent(self, e) -> None:  # noqa: N802 - Qt API
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().hideEvent(e)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        # Passive filter — installed ONLY while open and always returns False
        # (never consumes), so popup menus elsewhere keep working.
        try:
            if (event.type() == QEvent.Type.MouseButtonPress
                    and obj is not self
                    and not self.isAncestorOf(obj)):
                g = event.globalPosition().toPoint()
                if not self.geometry().contains(g):
                    self.close()
        except Exception:
            pass
        return False

    def show_centered(self) -> None:
        p = self._win.geometry()
        x = p.x() + (p.width() - self.width()) // 2
        y = p.y() + (p.height() - self.height()) // 2
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._name_edit.setFocus()

    # -- collect + save ----------------------------------------------------

    def _collect(self) -> dict:
        """Build the entry fields dict from the form. Raises ValueError on
        validation failure (message already shown to the user)."""
        name = self._name_edit.text().strip()
        if self._chosen is None:
            raise ValueError("no type chosen")
        if not name:
            QMessageBox.warning(self, "TriggerPanel", "Name is required.")
            self._name_edit.setFocus()
            raise ValueError("name missing")
        fields: dict = {"name": name, "type": self._chosen}
        icon = self._icon_edit.text().strip()
        if icon:
            fields["icon"] = icon
        if self._chosen == "app":
            path = self._path_edit.text().strip()
            if not path:
                QMessageBox.warning(self, "TriggerPanel",
                                    "Path / command is required.")
                self._path_edit.setFocus()
                raise ValueError("path missing")
            fields["path"] = path
        elif self._chosen == "script":
            script = self._script_edit.text().strip()
            if not script:
                QMessageBox.warning(self, "TriggerPanel",
                                    "Script path is required.")
                self._script_edit.setFocus()
                raise ValueError("script missing")
            fields["script"] = script
            venv = self._venv_edit.text().strip()
            if venv:
                fields["venv"] = venv
        elif self._chosen == "keys":
            keys = self._keys_edit.text().strip()
            if not keys:
                QMessageBox.warning(self, "TriggerPanel",
                                    "Keystroke sequence is required.")
                self._keys_edit.setFocus()
                raise ValueError("keys missing")
            try:
                parse_keys(keys)  # validate without sending
            except ValueError as e:
                QMessageBox.warning(self, "TriggerPanel",
                                    f"Invalid keystrokes:\n{e}")
                self._keys_edit.setFocus()
                raise ValueError(str(e)) from e
            fields["keys"] = keys
        elif self._chosen == "func":
            fields.update(self._func_fields())
        else:  # http
            url = self._url_edit.text().strip()
            if not url.startswith(("http://", "https://")):
                QMessageBox.warning(
                    self, "TriggerPanel",
                    "URL is required and must start with http:// or https://")
                self._url_edit.setFocus()
                raise ValueError("url missing")
            fields["url"] = url
            fields["method"] = self._method_box.currentText()
            fields["body"] = self._body_edit.text()
        return fields

    def _add(self) -> None:
        try:
            fields = self._collect()
        except ValueError:
            return  # warning already shown
        if self._edit_id is not None:
            update_entry(self._edit_id, fields)
            if self._on_added is not None:
                self._on_added(self._edit_id, fields["name"], True)
            self.close()
            return
        new_id = store_entry(fields)
        if self._on_added is not None:
            self._on_added(new_id, fields["name"], False)
        self.close()


# --------------------------------------------------------------------------- settings

STARTUP_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
BOOT_LNK = os.path.join(STARTUP_DIR, "TriggerPanel.lnk")
BOOT_PYTHON = os.path.join(BASE_DIR, "venv", "Scripts", "pythonw.exe")
EXE_PATH = os.path.join(BASE_DIR, "TriggerPanel.exe")  # built exe (project root)


def boot_enabled() -> bool:
    return os.path.exists(BOOT_LNK)


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def set_boot(enabled: bool) -> None:
    """Create/remove the Startup shortcut so TriggerPanel starts with Windows."""
    if enabled:
        # Prefer the built .exe (frozen self, or the root build when running
        # from source); fall back to launching server.py via pythonw.
        if FROZEN:
            target = sys.executable
            arguments = ""
        elif os.path.isfile(EXE_PATH):
            target = EXE_PATH
            arguments = ""
        else:
            target = BOOT_PYTHON
            arguments = chr(34) + os.path.abspath(__file__) + chr(34)
            if not os.path.isfile(target):
                raise RuntimeError(f"pythonw not found at {target}")
        ps = (
            "$ws = New-Object -ComObject WScript.Shell; "
            f"$l = $ws.CreateShortcut({_ps_quote(BOOT_LNK)}); "
            f"$l.TargetPath = {_ps_quote(target)}; "
            f"$l.Arguments = {_ps_quote(arguments)}; "
            f"$l.WorkingDirectory = {_ps_quote(BASE_DIR)}; "
            "$l.WindowStyle = 7; $l.Save()"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            creationflags=CREATE_NO_WINDOW,
            capture_output=True, timeout=20)
        if not os.path.exists(BOOT_LNK):
            err = r.stderr.decode(errors="replace")[:200] if r.stderr else ""
            raise RuntimeError(
                "could not create the Startup shortcut"
                + (f": {err}" if err else ""))
    else:
        try:
            os.remove(BOOT_LNK)
        except FileNotFoundError:
            pass


class SettingsOverlay(QWidget):
    """Frameless settings popup — same TradingBot overlay pattern as the wizard.

    Run at boot applies immediately (Startup shortcut file), the token
    applies immediately (/run reads config per request), the port binds on
    the next process start (offer a restart when it changes).
    """

    def __init__(self, win: TriggerWindow, on_saved=None) -> None:
        super().__init__(win, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Tool)
        self._win = win
        self._on_saved = on_saved  # callable(port_changed: bool, cfg: dict)

        self.setWindowTitle("Settings")
        self.setFixedSize(450, 300)
        self.setStyleSheet(WIZARD_STYLESHEET)
        _apply_overlay_chrome(self)

        cfg = load_config()
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 14)
        root.setSpacing(9)

        title_row = QHBoxLayout()
        title = QLabel("Settings")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        close_x = QPushButton("✕")
        close_x.setObjectName("titleClose")
        close_x.setFixedSize(26, 26)
        close_x.setCursor(Qt.CursorShape.PointingHandCursor)
        close_x.clicked.connect(self.close)
        title_row.addWidget(close_x)
        root.addLayout(title_row)

        self._boot_check = QCheckBox("Run at boot (start with Windows)")
        self._boot_check.setChecked(boot_enabled())
        root.addWidget(self._boot_check)

        port_row = QHBoxLayout()
        port_row.setSpacing(6)
        port_row.addWidget(QLabel("Port"))
        self._port_edit = QLineEdit(str(cfg["port"]))
        self._port_edit.setFixedWidth(90)
        port_row.addWidget(self._port_edit)
        port_hint = QLabel("restart to apply · re-run add-firewall.bat if it changes")
        port_hint.setObjectName("wizardHint")
        port_row.addWidget(port_hint)
        port_row.addStretch(1)
        root.addLayout(port_row)

        tok_row = QHBoxLayout()
        tok_row.setSpacing(6)
        tok_row.addWidget(QLabel("Token"))
        self._token_edit = QLineEdit(cfg["token"])
        tok_row.addWidget(self._token_edit, 1)
        gen_btn = QPushButton("Generate")
        gen_btn.clicked.connect(self._generate_token)
        tok_row.addWidget(gen_btn)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(lambda: self._copy_token(copy_btn))
        tok_row.addWidget(copy_btn)
        root.addLayout(tok_row)
        tok_hint = QLabel(
            "A new token applies immediately — copy your shortcut URLs again. "
            "Anyone with the token can trigger entries.")
        tok_hint.setObjectName("wizardHint")
        tok_hint.setWordWrap(True)
        root.addWidget(tok_hint)

        root.addStretch(1)
        foot = QHBoxLayout()
        foot.setSpacing(6)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.close)
        foot.addWidget(cancel_btn)
        foot.addStretch(1)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("accent")
        save_btn.clicked.connect(self._save)
        foot.addWidget(save_btn)
        root.addLayout(foot)

    def collect(self) -> tuple[int, str, bool]:
        """(port, token, run_at_boot) from the form; warns + raises if invalid."""
        port_s = self._port_edit.text().strip()
        if not port_s.isdigit() or not (1 <= int(port_s) <= 65535):
            QMessageBox.warning(self, "TriggerPanel",
                                "Port must be a number between 1 and 65535.")
            raise ValueError("port")
        token = self._token_edit.text().strip()
        if not token:
            QMessageBox.warning(self, "TriggerPanel",
                                "Token cannot be empty.")
            raise ValueError("token")
        return int(port_s), token, self._boot_check.isChecked()

    def _generate_token(self) -> None:
        self._token_edit.setText(secrets.token_urlsafe(16))
        self._token_edit.selectAll()
        self._token_edit.setFocus()

    def _copy_token(self, btn: QPushButton) -> None:
        QApplication.clipboard().setText(self._token_edit.text())
        original = btn.text()
        btn.setText("Copied!")
        QTimer.singleShot(1200, lambda: self._restore_btn(btn, original))

    @staticmethod
    def _restore_btn(btn: QPushButton, text: str) -> None:
        try:
            btn.setText(text)
        except RuntimeError:
            pass

    def _save(self) -> None:
        try:
            port, token, boot = self.collect()
        except ValueError:
            return  # warning already shown
        try:
            if boot != boot_enabled():
                set_boot(boot)
        except Exception as e:
            QMessageBox.warning(self, "TriggerPanel",
                                f"Could not update Run at boot:\n{e}")
            return
        with CONFIG_LOCK:
            cfg = load_config()
            port_changed = int(cfg["port"]) != port
            cfg["port"] = port
            cfg["token"] = token
            _write_config(cfg)
        log(f"settings saved (port={port}, boot={boot}, "
            f"token_changed={cfg['token'] != token})")
        if self._on_saved is not None:
            self._on_saved(port_changed, cfg)
        self.close()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Paint the card. A plain QWidget that IS the window root gets no
        background from its own QSS sheet, so the title row was never cleared
        between frames and any label there overpainted its own old text
        (the 'Step 1 of 2' ghost). Filling the rect is what erases it —
        same colours the QSS declares, so nothing can drift."""
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(MANTLE))
        p.setPen(QPen(QColor(OVERLAY), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    # -- dismissal (TradingBot overlay pattern) ---------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().showEvent(e)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def hideEvent(self, e) -> None:  # noqa: N802 - Qt API
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().hideEvent(e)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        try:
            if (event.type() == QEvent.Type.MouseButtonPress
                    and obj is not self
                    and not self.isAncestorOf(obj)):
                g = event.globalPosition().toPoint()
                if not self.geometry().contains(g):
                    self.close()
        except Exception:
            pass
        return False  # never consume

    def show_centered(self) -> None:
        p = self._win.geometry()
        x = p.x() + (p.width() - self.width()) // 2
        y = p.y() + (p.height() - self.height()) // 2
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._port_edit.setFocus()


# --------------------------------------------------------------------------- icon

def build_icon() -> QIcon:
    """Blue rounded tile with a dark play triangle — TradingBot accent."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(BLUE))
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setBrush(QColor(BASE))
    p.drawPolygon(QPolygonF([QPointF(24, 16), QPointF(50, 32),
                             QPointF(24, 48)]))
    p.end()
    return QIcon(pm)


def icon_png_bytes(icon: QIcon, size: int = 180) -> bytes | None:
    """Render the app icon to PNG bytes for <link rel="apple-touch-icon">."""
    try:
        # Source pixmap is 64px — scale up smoothly so iOS gets a crisp icon.
        pm = icon.pixmap(64, 64).scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        if not pm.save(buf, "PNG"):
            return None
        return bytes(buf.data())
    except Exception:
        return None


# --------------------------------------------------------------------------- main

def _excepthook(tp, val, tb):
    log("unhandled error:\n" + "".join(traceback.format_exception(tp, val, tb)))


def main() -> None:
    sys.excepthook = _excepthook
    cfg = load_config()
    port = int(cfg["port"])

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    app.setFont(QFont("Segoe UI", 10))
    icon = build_icon()
    app.setWindowIcon(icon)
    global ICON_PNG
    ICON_PNG = icon_png_bytes(icon)

    # Bind before showing the window so a port clash surfaces as a dialog.
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError as e:
        log(f"cannot bind 0.0.0.0:{port} — {e} (already running?)")
        QMessageBox.critical(
            None, "TriggerPanel",
            f"Port {port} is already in use — TriggerPanel is probably "
            "already running.")
        sys.exit(1)

    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"listening on 0.0.0.0:{port}  (http://{lan_ip()}:{port})")
    log(f"token: {cfg['token']}")

    win = TriggerWindow(icon)
    win.show()

    try:
        app.exec()
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        if _RESTART["pending"]:
            log("restarting with new settings…")
            cmd = [sys.executable] if FROZEN else \
                [sys.executable, os.path.abspath(__file__)]
            subprocess.Popen(cmd, cwd=BASE_DIR, creationflags=CREATE_NO_WINDOW)
        log("exited")


if __name__ == "__main__":
    main()

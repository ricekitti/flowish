"""Flowish: local, shortcut-driven Windows dictation."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import queue
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path
from urllib import error, request

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel


ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = ROOT / "settings.json"
SAMPLE_RATE = 16000
HOTKEY_ID = 1
MOD_CONTROL = 0x0002
MOD_ALT = 0x0001
VK_SPACE = 0x20
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.keybd_event.argtypes = (ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_size_t)


def load_settings() -> dict:
    defaults = {"model": "base", "language": None, "remove_fillers": True,
                "replacements": {}, "ai_cleanup": True, "ai_model": "gemini-2.5-flash-lite"}
    if SETTINGS_PATH.exists():
        try:
            incoming = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(incoming, dict):
                defaults.update(incoming)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Could not read settings.json: {exc}") from exc
    return defaults


def clean_text(text: str, settings: dict) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if settings.get("remove_fillers"):
        text = re.sub(r"(?i)(?<!\w)(?:um+|uh+|erm+)(?!\w)[, ]*", "", text)
        text = re.sub(r"\b(\w+)(?:\s+\1)+\b", r"\1", text, flags=re.IGNORECASE)
    for source, target in settings.get("replacements", {}).items():
        if source:
            text = re.sub(rf"\b{re.escape(source)}\b", str(target), text, flags=re.IGNORECASE)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1].upper() + text[1:] if text else text


def ai_cleanup(text: str, api_key: str, model: str = "gemini-2.5-flash-lite") -> str:
    """Send text only (never audio) to Gemini for conservative copy editing."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", model):
        raise ValueError("Invalid AI model name")
    prompt = (
        "Copy edit this speech transcript. Fix commas, punctuation, grammar, "
        "capitalization, and obvious spelling errors. Remove filler words and "
        "accidental repetitions. Preserve the speaker's meaning, language, "
        "tone, names, and numbers. Do not answer questions or add information. "
        "Return only a JSON object with one string field named text.\n\n"
        f"Transcript:\n{text}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    req = request.Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                          method="POST")
    try:
        with request.urlopen(req, timeout=30) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        raise RuntimeError(f"Gemini returned HTTP {exc.code}") from exc
    candidate = result["candidates"][0]
    if candidate.get("finishReason") != "STOP":
        raise RuntimeError("Gemini did not finish the edit")
    output = "".join(part.get("text", "") for part in candidate["content"]["parts"])
    edited = json.loads(output)["text"].strip()
    if not edited or len(edited) > max(200, len(text) * 2):
        raise RuntimeError("Gemini returned an invalid edit")
    return edited


class Flowish:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.root = tk.Tk()
        self.root.title("Flowish")
        self.root.geometry("600x390")
        self.root.minsize(560, 340)
        self.events: queue.Queue[tuple] = queue.Queue()
        self.frames: list[np.ndarray] = []
        self.stream = None
        self.recording = False
        self.busy = False
        self.model = None
        self.api_key = os.environ.get("GEMINI_API_KEY", "")
        self.hotkey_thread_id = None
        self.closed = False
        self._build_ui()
        self.root.after(50, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=self._hotkey_loop, daemon=True).start()
        threading.Thread(target=self._load_model, daemon=True).start()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Flowish", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Local voice typing • Ctrl+Alt+Space", font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 18))
        self.status = tk.StringVar(value="Loading speech model…")
        ttk.Label(outer, textvariable=self.status).pack(anchor="w")
        self.button = ttk.Button(outer, text="Start recording", command=lambda: self.toggle(False), state="disabled")
        self.button.pack(anchor="w", pady=12)
        self.ai_enabled = tk.BooleanVar(value=bool(self.settings.get("ai_cleanup") and self.api_key))
        ai_label = "AI cleanup: grammar, spelling, punctuation (sends text to Google)"
        self.ai_switch = ttk.Checkbutton(outer, text=ai_label, variable=self.ai_enabled)
        self.ai_switch.pack(anchor="w", pady=(0, 6))
        if not self.api_key:
            self.ai_switch.configure(state="disabled")
            ttk.Label(outer, text="Set GEMINI_API_KEY and restart to enable AI cleanup.").pack(anchor="w", pady=(0, 6))
        ttk.Label(outer, text="Last transcript").pack(anchor="w")
        self.transcript = tk.Text(outer, height=6, wrap="word")
        self.transcript.pack(fill="both", expand=True, pady=(5, 0))

    def _load_model(self) -> None:
        try:
            self.model = WhisperModel(self.settings["model"], device="cpu", compute_type="int8")
            self.events.put(("ready",))
        except Exception as exc:
            self.events.put(("error", f"Model load failed: {exc}"))

    def _hotkey_loop(self) -> None:
        self.hotkey_thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_SPACE):
            self.events.put(("error", "Ctrl+Alt+Space is already in use. The button still works."))
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.events.put(("hotkey",))
        user32.UnregisterHotKey(None, HOTKEY_ID)

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if status:
            self.events.put(("error", f"Microphone: {status}"))
        if self.recording:
            self.frames.append(indata[:, 0].copy())

    def toggle(self, paste: bool) -> None:
        if self.closed or self.busy or self.model is None:
            return
        if self.recording:
            self.recording = False
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as exc:
                self.status.set(f"Microphone error: {exc}")
            self.stream = None
            audio = np.concatenate(self.frames) if self.frames else np.empty(0, dtype=np.float32)
            self.frames = []
            self.busy = True
            self.button.configure(state="disabled", text="Transcribing…")
            self.status.set("Transcribing locally…")
            threading.Thread(target=self._transcribe,
                             args=(audio, paste, self.ai_enabled.get()), daemon=True).start()
        else:
            self.frames = []
            try:
                self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=self._audio_callback)
                self.stream.start()
            except Exception as exc:
                self.stream = None
                self.status.set(f"Microphone error: {exc}")
                return
            self.recording = True
            self.status.set("Recording… press Ctrl+Alt+Space again to paste")
            self.button.configure(text="Stop recording")

    def _transcribe(self, audio: np.ndarray, paste: bool, use_ai: bool) -> None:
        try:
            if len(audio) < SAMPLE_RATE // 4:
                self.events.put(("result", "", paste))
                return
            segments, _ = self.model.transcribe(audio, language=self.settings.get("language"), vad_filter=True)
            raw = " ".join(segment.text.strip() for segment in segments)
            text = clean_text(raw, self.settings)
            warning = None
            if text and use_ai and self.api_key:
                self.events.put(("status", "Improving grammar and punctuation…"))
                try:
                    text = ai_cleanup(text, self.api_key, self.settings.get("ai_model", "gemini-2.5-flash-lite"))
                except (ValueError, KeyError, IndexError, OSError, RuntimeError) as exc:
                    warning = f"AI cleanup unavailable ({exc}); pasted local transcript."
            self.events.put(("result", text, paste, warning))
        except Exception as exc:
            self.events.put(("error", f"Transcription failed: {exc}"))

    def _paste(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_V, 0, 0, 0)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

    def _poll(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "ready":
                    self.status.set("Ready. Focus a text field, then press Ctrl+Alt+Space.")
                    self.button.configure(state="normal")
                elif event[0] == "hotkey":
                    self.toggle(True)
                elif event[0] == "result":
                    _, text, paste, *extra = event
                    warning = extra[0] if extra else None
                    self.transcript.delete("1.0", "end")
                    self.transcript.insert("1.0", text)
                    if paste and text:
                        self._paste(text)
                    self.status.set(warning or ("Done." if text else "No speech detected."))
                    self.busy = False
                    self.button.configure(state="normal", text="Start recording")
                elif event[0] == "status":
                    self.status.set(event[1])
                elif event[0] == "error":
                    self.status.set(event[1])
                    self.busy = False
                    if self.model is not None:
                        self.button.configure(state="normal", text="Start recording")
        except queue.Empty:
            pass
        if not self.closed:
            self.root.after(50, self._poll)

    def close(self) -> None:
        self.closed = True
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
        if self.hotkey_thread_id:
            user32.PostThreadMessageW(self.hotkey_thread_id, WM_QUIT, 0, 0)
        self.root.destroy()


if __name__ == "__main__":
    try:
        Flowish().root.mainloop()
    except ValueError as exc:
        messagebox.showerror("Flowish", str(exc))


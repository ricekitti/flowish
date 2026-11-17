"""Flowish: local, shortcut-driven Windows dictation."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import queue
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path

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
    defaults = {"model": "base", "language": None, "remove_fillers": True, "replacements": {}}
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


class Flowish:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.root = tk.Tk()
        self.root.title("Flowish")
        self.root.geometry("500x330")
        self.root.minsize(400, 280)
        self.events: queue.Queue[tuple] = queue.Queue()
        self.frames: list[np.ndarray] = []
        self.stream = None
        self.recording = False
        self.busy = False
        self.model = None
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
            threading.Thread(target=self._transcribe, args=(audio, paste), daemon=True).start()
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

    def _transcribe(self, audio: np.ndarray, paste: bool) -> None:
        try:
            if len(audio) < SAMPLE_RATE // 4:
                self.events.put(("result", "", paste))
                return
            segments, _ = self.model.transcribe(audio, language=self.settings.get("language"), vad_filter=True)
            raw = " ".join(segment.text.strip() for segment in segments)
            self.events.put(("result", clean_text(raw, self.settings), paste))
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
                    _, text, paste = event
                    self.transcript.delete("1.0", "end")
                    self.transcript.insert("1.0", text)
                    if paste and text:
                        self._paste(text)
                    self.status.set("Done." if text else "No speech detected.")
                    self.busy = False
                    self.button.configure(state="normal", text="Start recording")
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


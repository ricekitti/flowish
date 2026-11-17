# Flowish

A small Windows voice dictation app inspired by Wispr Flow's core workflow. Press **Ctrl+Alt+Space**, speak, press it again, and the transcript is pasted into the field that had focus. Transcription runs locally with faster-whisper. Optional AI cleanup fixes punctuation, grammar, and spelling using Google's Gemini API. The speech model downloads on first use.

## Run

Requires Windows, Python 3.14, a microphone, and an internet connection for the first model download. In PowerShell, from this folder:

```powershell
.\launch.ps1
```

If PowerShell blocks scripts, run `powershell -ExecutionPolicy Bypass -File .\launch.ps1`.

Use the window's **Start/stop recording** button to test transcription without pasting. Use the global shortcut to paste into another app. The shortcut requires a focused text field. The app does not save audio or transcripts. It leaves the last transcript on the clipboard after pasting.

## Optional AI cleanup

Gemini 2.5 Flash-Lite has a limited free API tier. Create a key at [Google AI Studio](https://aistudio.google.com/apikey). In the PowerShell window where you will run the app, set it for that session, then launch:

```powershell
$env:GEMINI_API_KEY = Read-Host "Gemini API key"
.\launch.ps1
```

With a key present, AI cleanup is enabled by default. The window has a checkbox to turn it off at any time. The app sends **transcribed text**, not audio, to Google for cleanup. Google says free-tier content may be used to improve its products. If the key is missing or the API fails, Flowish uses the local transcript. The key is read from the environment and is never written to the repository or settings file. You can change the model in `settings.json`.

## Settings

Edit `settings.json`. `model` defaults to `base` (multilingual, CPU friendly). `language` can be `null` for auto-detection or a code such as `en`. `remove_fillers` removes simple standalone filler words; turn it off for literal transcription. `replacements` maps recognized words to your preferred spelling. Set `ai_cleanup` to `false` to keep AI off by default, even when a key is present.

## Scope

This prototype covers shortcut-based dictation, local transcription, optional AI cleanup, and paste into the active app. It does not include Wispr Flow's live transcription, per-app styles, mobile sync, or meeting notes. Some elevated apps may reject simulated paste from a normal-privilege process.


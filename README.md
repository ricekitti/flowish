# Flowish

A simple light weight windows dictation tool for taking notes or making chatgpt prompts.

## Run

Requires Windows, Python 3.14, a microphone, and an internet connection for the first model download. In PowerShell, from this folder:

```powershell
.\launch.ps1
```

If PowerShell blocks scripts, run `powershell -ExecutionPolicy Bypass -File .\launch.ps1`.

Use the window's **Start/stop recording** button to test transcription without pasting. Use the global shortcut to paste into another app. The shortcut requires a focused text field. The app does not save audio or transcripts. It leaves the last transcript on the clipboard after pasting.

## Settings

Edit `settings.json` after the first run. `model` defaults to `base` (multilingual, CPU friendly). `language` can be `null` for auto-detection or a code such as `en`. `remove_fillers` removes simple standalone filler words; turn it off for literal transcription. `replacements` maps recognized words to your preferred spelling.

## Scope

This prototype covers shortcut-based dictation, local transcription, light cleanup, and paste into the active app. It does not include Wispr Flow's live transcription, context-aware rewriting, per-app styles, mobile sync, or meeting notes. Some elevated apps may reject simulated paste from a normal-privilege process.


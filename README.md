<h1 align="center"><img src="ui/public/icon-mark.png" alt="" width="48" align="absmiddle"> Deban</h1>

English | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

Deban runs in your browser and transcribes meeting audio on your computer. You can save the recording, edit the transcript and search past meetings. An optional AI connection enables translation, suggested replies and meeting summaries.

![Deban live meeting with example data](assets/screenshot-en.png)

## Features

- **Live transcription and recording** — Capture audio shared from a browser tab, optionally include your microphone, and transcribe Japanese, Chinese or English locally. Save recordings and follow the transcript as the meeting progresses.
- **Speaker management** — Recognize speakers with local voiceprints, identify your own microphone channel, manage participants and correct speaker assignments.
- **Meeting assistance** — Configure an AI provider to translate speech, identify moments when you may want to respond, suggest replies in different tones, and generate topic summaries, decisions and action items.
- **Meeting context** — Add a meeting brief, personal profile, glossary and reference documents. Select materials and their priority for each meeting, and carry context forward from an earlier meeting.
- **Meeting history** — Search and review meetings, edit or split transcript entries, add notes, play saved audio, and export transcripts as Markdown or SRT.
- **Local data and backups** — Keep meeting data on your computer, manage deleted items, and export or import `.deban` archives. Choose whether to include voiceprints in a backup.
- **Language and AI settings** — Use the interface in English, Japanese, Simplified Chinese or Traditional Chinese. Connect your own AI provider and configure its model and key.

## Install and run

You need Python 3.12, uv, Node.js 22 with npm, and a recent Chrome or Edge. Run these commands from the repository directory.

```sh
uv sync --frozen
npm --prefix ui ci
npm --prefix ui run build
uv run --frozen python main.py
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The first launch downloads the local speech and speaker recognition models from Hugging Face. Allow about 600 MB for the models and additional space for dependencies and recordings. The service listens on loopback and is intended for a single computer.

## Record a meeting

1. Open the meeting in another browser tab.
2. In Deban, select the source language and choose whether to include your microphone and save the recording.
3. Start the meeting, select the meeting tab in the browser's sharing dialog and enable **Share tab audio**. Keep Deban open while it records. Deban processes the audio track from the share.
4. End the meeting and open **History** to view, edit or export the transcript.

The supported capture workflow is browser tab sharing. Audio from native meeting apps or other system sources depends on the browser and operating system.

## Connect AI

Open **Settings → AI connection** and choose OpenAI, Anthropic, Google Gemini, Grok, Kimi, DeepSeek, OpenRouter, OpenCode Zen or an OpenAI-compatible provider. Enter the model ID and API key, test the connection and save. For an OpenAI-compatible connection, enter the service's API base URL, such as `http://127.0.0.1:11434/v1` for a local service. Remote URLs use HTTPS.

The key is stored in the local server's data directory. The test sends a short test prompt. AI features send transcript text and, where relevant, document excerpts and profile information to the selected provider. Recordings and the transcription process stay local. Recording, transcription and history work without an AI connection.

## Data and migration

Data is stored under `~/.local/share/deban` by default, or under `$XDG_DATA_HOME/deban` when set. Set `MC_DATA_DIR` to use another directory. The backend log is named `app.log`.

To move data from an earlier Deban installation, download a `.deban` archive from the previous installation and import it under **Settings → Data & backup**. Imports deduplicate meetings. API keys and AI connection settings are excluded from archives.

See the [changelog](CHANGELOG.md) and [third-party notices](THIRD_PARTY_NOTICES.md).

## License

Deban is released under the [MIT License](LICENSE).

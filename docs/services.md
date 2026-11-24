# Services and models

[Documentation](README.md) · [Using the toolbar](usage.md)

## Services

| Service | Type | Port | Model | Licence |
|---|---|---|---|---|
| **Apple Speech** (default STT) | STT | 9465 | SpeechAnalyzer (built in) | macOS |
| Whisper | STT | 9565 | `mlx-community/whisper-large-v3-turbo` | MIT |
| Kyutai STT | STT | 9665 | `kyutai/stt-1b-en_fr-mlx` | CC-BY-4.0 |
| Parakeet | STT | 9765 | `mlx-community/parakeet-tdt-0.6b-v3` | CC-BY-4.0 |
| Nemotron | STT | 9865 | `mlx-community/nemotron-3.5-asr-streaming-0.6b` | Custom licence (`license:other`) |
| Moonshine | STT | 9965 | English only: model spec `medium-streaming@en` (downloaded by the backend from `download.moonshine.ai`, not the Hub) | MIT |
| **Kokoro** (default TTS) | TTS | 8765 | `mlx-community/Kokoro-82M-bf16` | Apache-2.0 |
| Phonon | TTS | 9365 | `gradium/phonon-31m-en` | Apache-2.0 |
| Pocket TTS | TTS | 8965 | `mlx-community/pocket-tts` | CC-BY-4.0 |
| Qwen3 TTS | TTS | 9165 | `mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-bf16` | Apache-2.0 |
| Voxtral TTS | TTS | 8865 | `mlx-community/Voxtral-4B-TTS-2603-mlx-bf16` | CC-BY-NC |
| Dia | TTS | 9065 | `mlx-community/Dia-1.6B-fp16` | Apache-2.0 |
| Fish S2 Pro | TTS | 9265 | `mlx-community/fish-audio-s2-pro` | research only |
| Tone | TTS | 8665 | test signal | — |

Some models come in several sizes or precisions. In **Add Models** a family
expands to list them, and each is its own entry with its own port, so siblings
can run side by side. Variants share their family's Python environment.

| Variant | Port | Model |
|---|---|---|
| Phonon 8-bit, 4-bit | 9366, 9367 | `phonon-q8`, `phonon-q4` (converted from Phonon's weights) |
| Whisper Large v3, Medium, Small, Base, Tiny | 9566–9570 | `mlx-community/whisper-{large-v3,medium,small,base,tiny}-mlx` |
| Kyutai STT 2.6B (English) | 9666 | `kyutai/stt-2.6b-en-mlx` |

TTS services speak the [TTS server protocol](https://github.com/vr000m/pipecat-local-tts-server/blob/78823eeabf1f36c464243af1bd41b879d058c028/docs/protocol.md) (`tts_server`). STT services speak the
[STT server protocol](https://github.com/vr000m/pipecat-local-stt-server/blob/v0.6.0/docs/protocol.md) (`stt_server`).
Any client of either works unchanged. The ports match the launchd agents the server repositories install
(`just tts-install` and `just stt-*`, run there, not in this checkout); see [Servers you didn't start](usage.md#servers-you-didnt-start)
for running both.

## How it works

- **TTS**: `uv pip install "pipecat-local-tts-server[<backend>] @ <bundled wheel>"`,
  pinned to `Contents/Resources/wheels/constraints.txt` in the app bundle, then
  `python -m tts_server prefetch` and `serve --supervised`. The build copies the pins from
  `server-pins/tts/constraints.txt` in the checkout.
- **Whisper, Kyutai, Parakeet, Nemotron, Moonshine**: PyPI packages hash-pinned to the last
  transitive dependency with `uv pip install --require-hashes`. The build copies each
  `stt-<engine>.txt` from `server-pins/stt/constraints/` in the checkout to
  `Contents/Resources/constraints/` in the app bundle. A bundled app refuses the install
  when the engine's file is missing. The upstream CLI runs the service
  (`python -m stt_server prefetch` and `serve --supervised`). The four default STT models (Whisper large-v3-turbo,
  Kyutai 1B, Parakeet, Nemotron) are pinned to a recorded Hub commit by upstream 0.6.0
  itself (`PINNED_REVISIONS`; `prefetch --json` reports the `revision`). The app passes
  no revision flag (there is none), only `--model <repo>`: a model other than a pinned
  default (the other Whisper sizes, Kyutai 2.6B) follows the repo's `main`. Moonshine is
  not a Hub model: its weights come from `download.moonshine.ai`, and the app passes the
  model spec as `--model`; for how those downloads are checked, see
  [Security notes](architecture.md#security-notes). Kyutai runs as a `moshi_mlx`
  backend inside `stt_server`.
  Parakeet needs `ffmpeg` installed (Homebrew: `/opt/homebrew/bin` or
  `/usr/local/bin`; the app adds those to Parakeet's server `PATH` only). Without
  it the server exits with `BackendStartError: FFmpeg is not installed or not in
  your PATH`.
- **Apple Speech**: SpeechAnalyzer in the app process, behind a Swift
  WebSocket server that speaks the same STT protocol.
- **Supervision**: every server prints a JSON `ready` line and exits when the
  app closes its stdin. Every event and error code is fixed in
  [`docs/supervised-contract.json`](supervised-contract.json).

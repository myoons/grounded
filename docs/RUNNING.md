# Run and reproduce Grounded

## Prerequisites

- Apple Silicon Mac, macOS 14.2+, Chrome, headphones and Xcode Command Line Tools.
- Python 3.12+ with `uv`, and Node/npm. CI uses Python 3.13 and Node 26.
- Runpod account/key, access to the public Kimi K3 endpoint, a deployed compatible
  Whisper endpoint, and a Firecrawl Search API key.
- Participant authorization before collecting meeting audio.
- macOS Keychain access for encrypted pending-audio storage. Capture fails closed
  if OS-backed encryption is unavailable; never bypass it with plaintext storage.

Commands below start from the repository root unless stated otherwise. Keep the
backend and warm-session processes running in their own terminals.

Pending audio is retained encrypted on the participant Mac until the server confirms
durable transcription. After a restart, choose **Resume processing** to recover it
without activating a microphone, or explicitly **Delete audio** to cancel it.
Incomplete saved checks can also be recovered when no audio remains. Recovery does
not reset permanent session limits: an exhausted budget requires an explicit
incomplete outcome, not an endless retry. Preserve/export needed text before deciding
to delete a failed meeting; local history is not a replicated backup. Review
[durable audio and retention](DURABLE_AUDIO.md) before recording real participants.

## 1. Configure private server credentials

```sh
mkdir -p ~/.config/roomshift
chmod 700 ~/.config/roomshift
```

Use a local editor to create these files, then set each to mode 600. Do not put real
keys in shell command arguments, this repository, screenshots or the desktop bundle.

| File under `~/.config/roomshift/` | Contents |
| --- | --- |
| `runpod.env` | `RUNPOD_API_KEY=YOUR_PRIVATE_KEY` |
| `search.env` | `FIRECRAWL_API_KEY=YOUR_PRIVATE_KEY` |
| `speech-deployment.json` | `{"endpoint_id":"YOUR_ENDPOINT_ID"}` for an existing compatible endpoint |

```sh
chmod 600 ~/.config/roomshift/runpod.env ~/.config/roomshift/search.env
uv sync --locked
```

If reusing an endpoint, also set `speech-deployment.json` to mode 600. If deploying
a new endpoint, do not pre-create that file: the deployment script writes it safely.
The endpoint ID is not a substitute for an API key. The service loads credentials
at startup and never sends them to the renderer. To create your own endpoint, see
the deployment section below instead of inventing an endpoint ID.

## 2. Terminal A — backend

```sh
uv run python -m scripts.runpod_serve --port 8877
```

This is a foreground process. Leave it running. The server binds only to localhost;
do not expose this single-operator API publicly. Its database is
`~/Library/Application Support/Roomshift/meetings.sqlite` on macOS. Use
`ROOMSHIFT_DATA_DIR=/absolute/private/path` to select a different data directory.

## 3. Terminal B — bounded GPU warmup

```sh
uv run python -m scripts.runpod_warm_session --seconds 1800
```

Leave this process running during the demonstration. One warm GPU can incur idle
charges. Ctrl-C or normal expiry restores the previous worker minimum. Concurrent
warm processes are rejected. If a process was killed abruptly, inspect its journal
and run `uv run python -m scripts.runpod_warm_session --restore` after it is no longer
running. Verify actual settings with `uv run python -m scripts.runpod_speech status`.

Before live speech, verify an actual inference. `speech_ready: true` only means the
service has configuration; provider health alone does not guarantee readiness.

```sh
uv run python -m scripts.prepare_meeting_fixtures
uv run python -m scripts.evaluate_chunk_boundary
```

Fixture creation uses macOS `say` and `ffmpeg`; install `ffmpeg` first if using this
optional test. It creates synthetic audio outside the repository. Never substitute
an unrelated real meeting recording for these fixtures.

## 4. Terminal C — desktop

```sh
cd desktop
npm ci
node build-native-audio.mjs
npm start
```

Allow Accessibility, Microphone and **System Audio Recording Only** through normal
OS prompts. No screen-recording picker is required. Development builds may need the
same app permission normally removed/re-added after replacement. Never edit TCC
databases or disable quarantine/security protections.

Join Google Meet in Chrome, then click its card in Grounded. Detection alone does
not record. Pause/Resume reconnects inputs; End stops capture and drains accepted processing.
Afterward, Meetings returns to the lobby without deleting results. Past meetings
open without recording or cloud inference. Built-in/headphone output is validated;
duplex output devices with physical input channels are rejected.

### A separate meeting MacBook

Run the backend/warmup on the server Mac. On the meeting MacBook, keep this tunnel
running in a separate terminal, replacing the placeholders with your SSH account:

```sh
ssh -N -L 8877:127.0.0.1:8877 USER@SERVER
```

Run/install the desktop on the MacBook. Its default API address is
`http://127.0.0.1:8877`. Saved history lives on the server Mac, not the MacBook.

## Deploy your own Whisper endpoint

The [handler](../speech_worker/handler.py) accepts bounded inline base64 PCM16 and
returns word timestamps, model/device identity and inference time. It does not accept
audio URLs. The [Dockerfile](../speech_worker/Dockerfile) contains CUDA, Python
3.12.13, hash-locked dependencies and the pinned model. Build needs substantial disk
space and model download time. The Kimi public endpoint requires no custom LLM worker.

```sh
docker buildx build --platform linux/amd64 -f speech_worker/Dockerfile \
  -t ghcr.io/YOUR_OWNER/grounded-speech:VERSION --push .
uv run python -m scripts.runpod_speech create \
  --image ghcr.io/YOUR_OWNER/grounded-speech@sha256:YOUR_AUDITED_DIGEST
uv run python -m scripts.runpod_speech status
```

Use lowercase registry names and the actual immutable digest. The creation script
refuses an existing deployment record to prevent duplicate paid resources. Default
minimum is zero, maximum one GPU. The [worker workflow](../speech_worker/build.yml)
is a template for a dedicated worker repository; application CI never creates GPUs.

## Tests and release build

No provider keys or GPU are needed for the unit suite:

```sh
uv run python -m unittest discover -s tests -q
uv run ruff check server scripts tests speech_worker
npm --prefix desktop test
cd desktop
node_modules/.bin/electron tests/discovery-ui-smoke.mjs
npm run package
```

The discovery smoke uses synthetic data. Actual-provider tests are documented in
[validation](VALIDATION.md); run them only against an isolated staging service.

The packaged app is `desktop/release/Roomshift Research-darwin-arm64/Roomshift Research.app`.
Packaging compiles/self-tests the Swift converter, includes allowlisted application
files, ad-hoc signs the complete bundle and requires deep strict signature verification.
It does not provide Developer ID signing or notarization. Local release ZIPs include
source commit and SHA256 manifests; the public repository contains source only.

## Common recovery paths

| Symptom | What to check |
| --- | --- |
| Connecting | Backend process and localhost/SSH tunnel. |
| Meet not detected | Accessibility for this exact app; Chrome meeting visible initially. |
| Microphone only | System Audio Recording permission, output device, then Pause/Resume. |
| Meeting audio only | Microphone permission; remote playback can still work independently. |
| Checks paused | Detector error; use Retry checks after restoring provider connectivity. |
| One card failed | Retry that card; capture can continue. |
| Save warning | Disk/DB availability; keep the session open until saving succeeds. |
| Empty history after upgrade | Meetings cleared in the former memory-only version cannot be recovered. |

Never patch the installed binary to work around a failure. Report the release
version, failing step and diagnostic code without secrets or private meeting content.

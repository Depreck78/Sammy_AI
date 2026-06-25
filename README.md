# Sammy

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

Sammy is a local, private AI agent for Ollama models — your chats and data stay on your own machine. It includes a FastAPI backend, React + Tailwind frontend, SQLite chat/settings storage, encrypted plugin credentials, and a plugin registry.

> **Platforms:** Sammy's app (web UI + Python backend + Ollama) runs on **macOS, Linux, and Windows**. The one-command installer and the `sammy` CLI below are tuned for **macOS**; on Linux/Windows, run it manually — see [Run on Linux / Windows](#run-on-linux--windows-manual).

## System requirements

Sammy's backend and UI are lightweight — the real requirement is **Ollama running a local model**, which is bound mostly by **RAM** (and, for speed, the GPU / Apple Silicon). `setup.sh` auto-selects the base model to fit your RAM:

| Detected RAM | Base model | Experience |
| --- | --- | --- |
| **≥ 16 GB** | `gemma2:9b` (default) | Sammy running well |
| **≥ 12 GB** | `llama3.1:8b` | Good, slightly smaller model |
| **< 12 GB** | `llama3.2:3b` | Runs, but a weaker/faster fallback |

**Recommended (runs well):** 16 GB+ RAM on an **Apple Silicon Mac (M1 or newer)**, where unified memory is shared with the GPU. 24–32 GB gives headroom for other apps and larger context windows.

**Minimum (runs at all):** 8 GB RAM, which uses the `llama3.2:3b` fallback — fine for trying Sammy, weaker for real tool use.

**Also good to know:**
- **Disk:** ~10 GB free — Ollama plus a base model is several GB (3B ≈ 2 GB, 8–9B ≈ 5–6 GB); the custom `sammy` model is a thin layer on top.
- **Mac chip matters:** Apple Silicon is GPU-accelerated and smooth; **Intel Macs run CPU-only and are slow**, even with enough RAM.
- **PC / Linux:** runs fine; for good speed use a dedicated GPU with ~8 GB VRAM for the 8–9B models, otherwise it falls back to CPU + system RAM.
- **Offline by design:** once models are pulled, core chat needs no internet. Only optional pieces reach out — web search, Gmail/Zoho tools, and ElevenLabs TTS (the Eagle voice-auth model runs locally in your browser).
- **Browser:** voice input (Web Speech API) needs **Chrome or Edge**; text chat works in any modern browser.

Override the auto-picked model with `SAMMY_MODEL=llama3.1:8b ./setup.sh`.

## Install (macOS)

**One line, from scratch:**

```bash
curl -fsSL https://raw.githubusercontent.com/Depreck78/Sammy_AI/main/install.sh | bash
```

That clones the repo and runs `setup.sh`. If you already have the repo, just run:

```bash
./setup.sh
```

`setup.sh` does everything and then **launches Sammy automatically**:
- installs **Ollama** if missing (via [Homebrew](https://brew.sh) on macOS) and starts it,
- pulls a base model **sized to your RAM** — `gemma2:9b` (≥16 GB), `llama3.1:8b` (≥12 GB), or `llama3.2:3b` (less),
- builds a custom **`sammy`** model (the base + Sammy's personality) and sets it as the default,
- builds the app and links the `sammy` command.

Pin a specific base model (skips the RAM check) with:

```bash
SAMMY_MODEL=llama3.1:8b ./setup.sh
```

After the first run, start/focus Sammy anytime with:

```bash
sammy
```

The setup also installs a capitalized alias:

```bash
Sammy
```

Useful commands:

```bash
sammy restart
sammy stop
sammy status
sammy logs
sammy uninstall        # remove Sammy (asks before deleting your data; --purge / --keep-data to skip the prompt)
```

### Uninstall

```bash
sammy uninstall
```

Stops Sammy, removes the launch agent and the `sammy`/`Sammy` commands, and asks whether to delete your local data (`~/.sammy` — chats, settings, encrypted credentials). Use `sammy uninstall --purge` to also delete data, or `--keep-data` to keep it. It leaves the project folder and Ollama/models in place (remove those manually if you want).

To use Sammy from your phone, turn on network access — it **stays on across restarts and reboots**:

```bash
sammy lan      # turn phone/LAN access ON (persists)
sammy local    # turn it back OFF (localhost only)
```

`sammy lan` restarts Sammy bound to your network, keeps the same local database/session, and prints two `Phone URL`s: a **fixed** one — Sammy auto-advertises `http://sammy.local:3131` — and a raw-IP one (e.g. `http://192.168.1.23:3131`). Prefer the fixed `.local` URL — it keeps working when your IP changes, as long as the phone is on the same Wi-Fi. Sammy also shows a Tailscale link for off-Wi-Fi access in the in-app popup (tap Sammy's name). Your Mac must stay awake and Ollama must keep running.

LAN mode exposes Sammy to devices that can reach your Mac, so a **login password is required** (Sammy prompts you to set one on first run). The CLI asks for confirmation the first time you enable LAN mode; after that the preference is saved (a `~/.sammy/lan-enabled` flag) so restarts don't re-prompt. Use `sammy local` to turn access off again. For non-interactive automation, set `SAMMY_ALLOW_NETWORK=1` only when you understand the risk and are on a trusted private network.

If this project lives inside `~/Desktop`, `~/Documents`, or `~/Downloads`, Sammy starts from your user shell instead of a macOS LaunchAgent. That avoids macOS privacy restrictions that can prevent background services from reading the local virtualenv.

## Run on Linux / Windows (manual)

The app is cross-platform — it's a Python server + a browser UI. The `setup.sh` installer and the `sammy` CLI are macOS-tuned, so on Linux/Windows run it directly:

1. **Install prerequisites:** Python 3, Node.js + npm, and [Ollama](https://ollama.com/download). Then pull a model: `ollama pull gemma2:9b` (and make sure `ollama serve` is running).
2. **Backend deps:**
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
   ```
3. **Build the UI:**
   ```bash
   cd frontend && npm install && npm run build && cd ..
   ```
4. **Run Sammy** (use `127.0.0.1` for local-only, or `0.0.0.0` for phone/LAN access):
   ```bash
   cd backend
   SAMMY_HOST=127.0.0.1 ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 3131
   # Windows: set SAMMY_HOST=0.0.0.0 && ..\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 3131
   ```
5. Open **http://localhost:3131**.

Notes for non-macOS:
- Phone/LAN access: start with `--host 0.0.0.0` (the `sammy lan`/`local` toggle is macOS-only). The `sammy.local` name still auto-advertises (via `zeroconf`) on Linux/Windows.
- macOS-only conveniences not available elsewhere: the `sammy` CLI (`lan`/`local`/`serve`, launch-agent autostart, browser auto-open) and the Mac's own Bonjour hostname detection.

## Requirements

- **macOS** for the one-command install (`./setup.sh`) + the `sammy` CLI — needs [Homebrew](https://brew.sh); Python 3, Node.js + npm, and Ollama are auto-installed if missing.
- **Linux / Windows:** run manually (above) with Python 3, Node.js + npm, and [Ollama](https://ollama.com/download).

In all cases Sammy talks to Ollama locally at `http://127.0.0.1:11434`.

## What It Does

- Lists local Ollama models from `http://127.0.0.1:11434`
- Streams model responses into a Sammy chat UI
- Stores conversations, settings, agents, plugin choices, and credentials in SQLite
- Supports Sammy plugins from `backend/tools`
- Includes built-in Gmail, Google Contacts, Zoho CRM, File System, and Web Search plugins
- Encrypts credential records with a local key in `~/.sammy`

## Security Model

Sammy is designed as a local single-user app. You can add a local login password from Settings > General; Sammy stores a password hash and uses an HttpOnly session cookie after login. Keep the default `SAMMY_HOST=127.0.0.1` unless you intentionally need same-Wi-Fi access.

Without a login password, anyone who can reach Sammy over the network can use the app, upload files, read visible chat data, change local settings, and prompt enabled tools to act. Enabled email, filesystem, and external plugins can perform real actions with your configured credentials. Only run `sammy lan` on a trusted private network, and turn it off with `sammy stop` when you are done.

The File System plugin is read-only by default and is limited to this repository unless you configure other allowed directories in Settings. File writes are hidden from the model until you explicitly enable `Allow file writes` for that plugin.

## Sammy Plugins

Add a new Python module to `backend/tools` with a class that extends `app.tooling.BaseTool`.

```python
class MyTool(BaseTool):
    name = "my_tool"
    display_name = "My Tool"
    description = "Do something useful."
    icon = "Wrench"
    requires_auth = False

    def get_functions(self):
        return [
            self.function(
                "my_tool_run",
                "Run the tool.",
                {"text": {"type": "string"}},
                ["text"],
            )
        ]

    def execute(self, function_name, parameters):
        return parameters["text"]
```

Restart Sammy and the plugin appears in Settings.

## External Sammy Plugins

Sammy does not require Codex to be installed. Built-in plugins live in `backend/tools`, and external plugins can be installed under `~/.sammy/plugins` or local development folders under `~/plugins/*`.

External plugins can use Sammy's native `.sammy-plugin/plugin.json` manifest folder. Sammy also reads the compatible `.codex-plugin/plugin.json` shape so existing local plugin bundles can be reused without Codex.

- `skills` are shown in Settings and injected into the agent context when the plugin is enabled.
- `mcpServers` from `.mcp.json` are exposed as callable plugin functions through a lightweight MCP stdio bridge.
- `apps` from `.app.json` are shown as connector metadata. They are not directly callable unless Sammy has a matching native bridge.
- Local plugins in `~/plugins/*` override plugins with the same manifest `name` from `~/.sammy/plugins`.

Set `SAMMY_PLUGIN_HOME` to change the installed Sammy plugin folder, `SAMMY_LOCAL_PLUGIN_HOME` to scan a different local development plugin folder, or `SAMMY_PLUGIN_PATHS` to add explicit plugin roots or parent folders.

For example, `~/plugins/zoho-mail` exposes its MCP functions directly in Sammy even on a computer with no Codex installation.

If you intentionally want to import an existing Codex cache as an optional compatibility source, start Sammy with `SAMMY_INCLUDE_CODEX_CACHE=1`. That is disabled by default.

## Privacy and Local Data

Sammy stores its SQLite database, encrypted credential key, uploads, and logs under `~/.sammy` by default. Do not commit that folder or any `.env` file. The repository includes `.env.example` for non-secret configuration names only.

## License

Sammy is free and open source under the **GNU Affero General Public License v3.0 (AGPL-3.0)** — see [`LICENSE`](LICENSE).

In short: you're free to use, study, modify, and share Sammy — but any distributed **or network-hosted** modified version must also make its complete source available under the same license. That "network use" clause is what keeps Sammy (and anything built on it) open, even when offered as a hosted service.

Copyright © 2026 the Sammy authors.

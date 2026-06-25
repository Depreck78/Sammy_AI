#!/usr/bin/env bash
set -euo pipefail

# One-command Sammy installer: sets up the app, installs Ollama, and pulls a default model.
# Override the model with:  SAMMY_MODEL=llama3.1:8b ./setup.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAMMY_MODEL_NAME="sammy"                  # the custom model Sammy uses by default
OS="$(uname)"

have() { command -v "$1" >/dev/null 2>&1; }

total_ram_gb() {
  if [ "$OS" = "Darwin" ]; then
    echo $(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024 / 1024 / 1024 ))
  else
    echo $(( $(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0) / 1024 / 1024 ))
  fi
}

echo "Setting up Sammy in $ROOT_DIR"

# Pick a base model that fits this machine, unless one is pinned via SAMMY_MODEL.
RAM_GB="$(total_ram_gb)"
if [ -z "${SAMMY_MODEL:-}" ]; then
  if [ "$RAM_GB" -ge 16 ]; then
    SAMMY_MODEL="gemma2:9b"      # roomy: full ~9B
  elif [ "$RAM_GB" -ge 12 ]; then
    SAMMY_MODEL="llama3.1:8b"    # mid: ~8B
  else
    SAMMY_MODEL="llama3.2:3b"    # tight: small + fast, runs on low-RAM Macs
  fi
  echo "Detected ${RAM_GB} GB RAM -> base model: $SAMMY_MODEL"
else
  echo "Using base model: $SAMMY_MODEL (pinned via SAMMY_MODEL)"
fi

# --- Prerequisites: Python 3 + Node (auto-installed via Homebrew on macOS) ---
ensure_prereqs() {
  if [ "$OS" = "Darwin" ] && ! have brew && { ! have python3 || ! have node || ! have ollama; }; then
    echo "Homebrew is required to auto-install Python / Node / Ollama."
    echo "Install it from https://brew.sh and re-run ./setup.sh"
    exit 1
  fi
  if ! have python3; then
    if [ "$OS" = "Darwin" ]; then brew install python; else echo "Please install Python 3."; exit 1; fi
  fi
  if ! have node || ! have npm; then
    if [ "$OS" = "Darwin" ]; then brew install node; else echo "Please install Node.js (https://nodejs.org)."; exit 1; fi
  fi
}

# --- Ollama (Sammy's local model runtime) ---
ensure_ollama() {
  if have ollama; then return; fi
  echo "Installing Ollama..."
  if [ "$OS" = "Darwin" ]; then
    brew install ollama
  else
    curl -fsSL https://ollama.com/install.sh | sh
  fi
}

start_ollama() {
  curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1 && return
  echo "Starting Ollama..."
  if [ "$OS" = "Darwin" ] && have brew; then
    brew services start ollama >/dev/null 2>&1 || true
    sleep 2
  fi
  if ! curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    nohup ollama serve >/tmp/sammy-ollama.log 2>&1 &
    disown 2>/dev/null || true
  fi
  for _ in $(seq 1 30); do
    curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1 && return
    sleep 1
  done
  echo "Ollama did not become ready; see /tmp/sammy-ollama.log"
  exit 1
}

pull_model() {
  echo "Downloading base model $SAMMY_MODEL (several GB — this is the long part)..."
  ollama pull "$SAMMY_MODEL"
}

build_sammy_model() {
  echo "Building the custom '$SAMMY_MODEL_NAME' model (Sammy's personality on $SAMMY_MODEL)..."
  local tmp
  tmp="$(mktemp)"
  sed "s|^FROM .*|FROM $SAMMY_MODEL|" "$ROOT_DIR/Modelfile" > "$tmp"
  ollama create "$SAMMY_MODEL_NAME" -f "$tmp"
  rm -f "$tmp"
}

set_tailscale_alias() {
  # Best-effort: if Tailscale is installed AND logged in, label this device "sammy" so the
  # tailnet link becomes sammy.<tailnet>.ts.net. This only changes the Tailscale-internal name,
  # NOT the Mac's real hostname. Skipped silently when Tailscale isn't set up.
  local ts dns
  ts="$(command -v tailscale || true)"
  [ -z "$ts" ] && [ -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ] && ts="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
  [ -z "$ts" ] && return 0
  dns="$("$ts" status --json 2>/dev/null | python3 -c 'import sys, json
try:
    print((json.load(sys.stdin).get("Self") or {}).get("DNSName", "").rstrip("."))
except Exception:
    pass' 2>/dev/null || true)"
  [ -z "$dns" ] && return 0          # not logged in
  case "$dns" in sammy.*) return 0 ;; esac   # already named sammy
  if "$ts" set --hostname=sammy >/dev/null 2>&1; then
    echo "Set this device's Tailscale name to 'sammy' (link: sammy.<your-tailnet>.ts.net)."
  fi
}

ensure_prereqs

# --- Sammy app: Python env + frontend build ---
python3 -m venv "$ROOT_DIR/.venv"
"$ROOT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$ROOT_DIR/.venv/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"

cd "$ROOT_DIR/frontend"
npm install
npm run build
cd "$ROOT_DIR"

# --- Ollama + default model ---
ensure_ollama
start_ollama
pull_model
build_sammy_model
set_tailscale_alias

# Make the custom "sammy" model Sammy's default (best-effort; otherwise Sammy auto-selects it).
(cd "$ROOT_DIR/backend" && "$ROOT_DIR/.venv/bin/python" -c \
  "from app import db; db.init_db(); db.update_settings({'default_model': '${SAMMY_MODEL_NAME}:latest'})") \
  2>/dev/null || echo "(could not preset default model; Sammy will auto-select it)"

chmod +x "$ROOT_DIR/scripts/sammy"

install_link() {
  local name="$1"
  local target="/usr/local/bin/$name"
  if [ -w "/usr/local/bin" ]; then
    ln -sf "$ROOT_DIR/scripts/sammy" "$target"
  elif [ -t 0 ]; then
    sudo ln -sf "$ROOT_DIR/scripts/sammy" "$target"
  else
    mkdir -p "$HOME/.local/bin"
    ln -sf "$ROOT_DIR/scripts/sammy" "$HOME/.local/bin/$name"
    echo "Installed $name to $HOME/.local/bin because /usr/local/bin needs sudo."
  fi
}

install_link sammy
install_link Sammy

echo
echo "Sammy is installed — with Ollama and the '$SAMMY_MODEL_NAME' model ready to go."
echo "Starting Sammy..."
"$ROOT_DIR/scripts/sammy" || true

cat <<EOF

Sammy is running at http://localhost:3131 (Ollama at http://127.0.0.1:11434).

Useful commands:
  sammy            start (or focus) Sammy
  sammy lan        enable same-Wi-Fi / phone access
  sammy restart    restart it
EOF

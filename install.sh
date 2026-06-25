#!/usr/bin/env bash
set -euo pipefail

# Remote one-line installer. Install Sammy with:
#   curl -fsSL https://raw.githubusercontent.com/Depreck78/Sammy_AI/main/install.sh | bash
#
# This clones (or updates) the repo, then runs setup.sh, which installs Ollama,
# pulls the base model, builds the custom "sammy" model, and launches Sammy.

# Override with SAMMY_REPO=... to install from a fork.
SAMMY_REPO="${SAMMY_REPO:-https://github.com/Depreck78/Sammy_AI.git}"
INSTALL_DIR="${SAMMY_DIR:-$HOME/Sammy}"

have() { command -v "$1" >/dev/null 2>&1; }

if ! have git; then
  echo "git is required. On macOS run:  xcode-select --install"
  exit 1
fi

if [ -d "$INSTALL_DIR/.git" ]; then
  echo "Updating Sammy in $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "Cloning Sammy into $INSTALL_DIR..."
  git clone "$SAMMY_REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
exec ./setup.sh

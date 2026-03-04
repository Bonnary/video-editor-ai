#!/usr/bin/env bash
# start.sh - ensure uv tool installed, venv synced, then activate and run app

set -e

OS="$(uname -s)"

# ── ffmpeg ────────────────────────────────────────────────────────────────────
echo "Checking for ffmpeg..."
if command -v ffmpeg &>/dev/null; then
    echo "ffmpeg is already available."
else
    echo "ffmpeg not found. Attempting to install..."
    if [[ "$OS" == "Darwin" ]]; then
        if ! command -v brew &>/dev/null; then
            echo "Homebrew not found. Installing Homebrew..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            # Add Homebrew to PATH for Apple Silicon and Intel Macs
            if [[ -f "/opt/homebrew/bin/brew" ]]; then
                eval "$(/opt/homebrew/bin/brew shellenv)"
            elif [[ -f "/usr/local/bin/brew" ]]; then
                eval "$(/usr/local/bin/brew shellenv)"
            fi
        fi
        brew install ffmpeg
    elif [[ "$OS" == "Linux" ]]; then
        if command -v apt-get &>/dev/null; then
            sudo apt-get update && sudo apt-get install -y ffmpeg
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y ffmpeg
        elif command -v pacman &>/dev/null; then
            sudo pacman -Sy --noconfirm ffmpeg
        else
            echo "Could not detect a supported package manager. Please install ffmpeg manually."
            exit 1
        fi
    else
        echo "Unsupported OS: $OS. Please install ffmpeg manually."
        exit 1
    fi
fi

# ── uv ────────────────────────────────────────────────────────────────────────
echo "Checking for uv..."
if command -v uv &>/dev/null; then
    echo "uv is already installed."
else
    echo "uv not found. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Pick up the newly installed uv binary
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

    if ! command -v uv &>/dev/null; then
        echo "uv installation failed. Please install manually and re-run."
        exit 1
    fi
    echo "uv installed successfully."
fi

# ── virtual environment ───────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Verifying virtual environment..."
if [[ ! -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
    echo "Virtual environment not found. Running uv sync to create it..."
else
    echo "Virtual environment already exists. Syncing dependencies..."
fi
uv sync

# ── run ───────────────────────────────────────────────────────────────────────
echo "Activating virtual environment..."
# shellcheck disable=SC1091
source "$SCRIPT_DIR/.venv/bin/activate"

echo "Starting application..."
python "$SCRIPT_DIR/main.py"

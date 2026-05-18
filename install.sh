#!/usr/bin/env bash
# Installer for mplayer-tui.
#
# Checks runtime dependencies, creates the config directory with a
# default config.json, and installs the `music` command into
# /usr/local/bin.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$HOME/.config/mplayer-tui"
BIN_PATH="/usr/local/bin/music"

echo "==> Checking dependencies..."
missing=()
for dep in python3 mpv yt-dlp tmux; do
    if ! command -v "$dep" >/dev/null 2>&1; then
        missing+=("$dep")
    fi
done
if (( ${#missing[@]} )); then
    echo "    Missing dependencies: ${missing[*]}"
    echo "    Install them with: sudo pacman -S ${missing[*]}"
    exit 1
fi
# curses ships with the Python standard library on Arch Linux.
if ! python3 -c "import curses" >/dev/null 2>&1; then
    echo "    Python 'curses' module is unavailable."
    exit 1
fi
echo "    all dependencies found."

echo "==> Creating config directory at $CONFIG_DIR..."
mkdir -p "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/config.json" ]]; then
    cat > "$CONFIG_DIR/config.json" <<EOF
{
  "browser": "firefox",
  "music_path": "$HOME/music",
  "default_volume": 80,
  "theme": "default"
}
EOF
    echo "    wrote default config.json"
else
    echo "    config.json already exists - leaving it untouched."
fi
mkdir -p "$HOME/music"

echo "==> Installing 'music' command to $BIN_PATH..."
launcher="$(mktemp)"
cat > "$launcher" <<EOF
#!/usr/bin/env bash
exec python3 "$SCRIPT_DIR/main.py" "\$@"
EOF
chmod +x "$launcher"
if [[ -w "$(dirname "$BIN_PATH")" ]]; then
    mv "$launcher" "$BIN_PATH"
else
    sudo mv "$launcher" "$BIN_PATH"
fi

echo ""
echo "Installation complete."
echo "  Run 'music'      to start the player."
echo "  Run 'music stop' to stop playback."
echo "  Detach anytime with Ctrl+B then D - audio keeps playing."

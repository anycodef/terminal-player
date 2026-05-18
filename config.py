"""Configuration management for mplayer-tui.

Loads and saves the user configuration stored in
``~/.config/mplayer-tui/config.json``. Missing files are created with
sensible defaults on first run so the player works out of the box.
"""

import json
import os

# Base directory for all persistent data (config, library, history).
CONFIG_DIR = os.path.expanduser("~/.config/mplayer-tui")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Default configuration written on first run.
DEFAULT_CONFIG = {
    "browser": "firefox",                          # --cookies-from-browser
    "music_path": os.path.expanduser("~/music"),   # download destination
    "default_volume": 80,                          # startup volume (0-130)
    "theme": "default",                            # color theme name
}


def ensure_dirs():
    """Create the config directory if it does not exist."""
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_config():
    """Return the user config dict, creating the default file if needed."""
    ensure_dirs()
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable config: fall back to defaults in memory
        # without destroying the file on disk.
        return dict(DEFAULT_CONFIG)
    # Merge with defaults so newly added keys are always present.
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def save_config(config):
    """Persist the given config dict to disk and ensure the music path."""
    ensure_dirs()
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
    music_path = os.path.expanduser(config.get("music_path", "~/music"))
    os.makedirs(music_path, exist_ok=True)

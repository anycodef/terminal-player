#!/usr/bin/env python3
"""mplayer-tui entry point and tmux session management.

Usage:
  music         launch the player, or reattach to a running session
  music stop    stop playback and kill the background session

When tmux is available the TUI runs inside a detached session named
``music``. The user can detach with Ctrl+B D and the audio keeps
playing; running ``music`` again reattaches to it.
"""

import os
import shutil
import subprocess
import sys

# Make the sibling modules importable regardless of the caller's cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import curses

from config import load_config
from library import Library
from player import Player, IPC_SOCKET
from downloader import Downloader
from tui import TUI

SESSION = "music"


def have(cmd):
    """Return True if ``cmd`` is on PATH."""
    return shutil.which(cmd) is not None


def tmux_session_exists():
    """Return True if the background tmux session is already running."""
    return subprocess.run(
        ["tmux", "has-session", "-t", SESSION],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def _kill_session():
    """Kill the tmux session if it exists."""
    if have("tmux") and tmux_session_exists():
        subprocess.run(["tmux", "kill-session", "-t", SESSION],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _curses_main(stdscr, library, player, downloader, config):
    """curses.wrapper target: run the TUI and report the quit mode."""
    tui = TUI(stdscr, library, player, downloader, config)
    tui.run()
    return tui.quit_audio


def run_tui():
    """Build the player components and run the curses interface."""
    config = load_config()
    library = Library()
    player = Player(default_volume=config.get("default_volume", 80))
    downloader = Downloader(
        browser=config.get("browser", "firefox"),
        music_path=config.get("music_path", "~/music"),
    )
    try:
        # Connect to (or launch) mpv; the TUI shows "connecting..."
        # and keeps retrying if this fails.
        player.start()
    except RuntimeError:
        pass

    quit_audio = curses.wrapper(
        _curses_main, library, player, downloader, config)

    if quit_audio:
        # 'Q' - stop the audio and tear the session down.
        player.quit_all()
        _kill_session()
    else:
        # 'q' - just detach; mpv keeps playing in the background.
        player.detach()


def stop():
    """Implement ``music stop``: quit mpv and kill the tmux session."""
    if os.path.exists(IPC_SOCKET):
        try:
            import json
            import socket
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(IPC_SOCKET)
            sock.sendall(json.dumps({"command": ["quit"]}).encode() + b"\n")
            sock.close()
        except OSError:
            pass
    _kill_session()
    print("music: stopped")


def main():
    """Dispatch the ``music`` command."""
    args = sys.argv[1:]
    if args and args[0] == "stop":
        stop()
        return

    script = os.path.abspath(__file__)

    if not have("tmux"):
        # Fallback: no session management, run the TUI directly.
        print("Warning: tmux not found - running without a "
              "background session.")
        run_tui()
        return

    if tmux_session_exists():
        # A session is already running: reattach to it.
        os.execvp("tmux", ["tmux", "attach", "-t", SESSION])
    elif os.environ.get("TMUX"):
        # Already inside tmux (cannot nest an attach): run here.
        run_tui()
    else:
        # Create a detached session running the TUI, then attach to it.
        subprocess.run([
            "tmux", "new-session", "-d", "-s", SESSION,
            sys.executable, script, "--inner",
        ])
        os.execvp("tmux", ["tmux", "attach", "-t", SESSION])


if __name__ == "__main__":
    # "--inner" is passed when this script is re-launched inside the
    # tmux session; it runs the TUI directly without re-dispatching.
    if "--inner" in sys.argv:
        run_tui()
    else:
        main()

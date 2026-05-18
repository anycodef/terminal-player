"""mpv audio backend controlled over a JSON IPC socket.

The :class:`Player` owns the mpv subprocess and a queue of tracks.
Playback control (play/pause/seek/volume) is sent as JSON commands over
a unix socket, which means mpv keeps running and playing audio even
after the curses TUI detaches or exits.
"""

import json
import os
import random
import socket
import subprocess
import threading
import time

# Shared IPC socket path used by both the player and ``music stop``.
IPC_SOCKET = "/tmp/mpv-music.sock"


class Player:
    def __init__(self, default_volume=80):
        self.socket_path = IPC_SOCKET
        self.default_volume = default_volume

        self._sock = None
        self._lock = threading.Lock()       # serialises socket writes
        self._req_id = 0
        self._pending = {}                  # request_id -> (Event, [result])
        self._reader = None
        self._running = False

        # Queue state managed by the player itself.
        self.queue = []                     # list of track dicts
        self.index = -1                     # current index into queue
        self.loop_track = False
        self.loop_playlist = False
        self.shuffle = False

        # Callbacks wired up by the TUI.
        self.resolver = None                # track -> direct stream URL
        self.on_track_change = None         # track -> None

    # --- process / connection ------------------------------------------
    def start(self):
        """Launch mpv if needed and connect to its IPC socket."""
        if self.connected():
            return
        if not self._can_connect():
            self._spawn_mpv()
        self._connect()

    def connected(self):
        """True when the IPC socket is open and the reader is alive."""
        return self._running and self._sock is not None

    def _can_connect(self):
        """Probe whether an mpv IPC socket is already accepting clients."""
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.connect(self.socket_path)
            probe.close()
            return True
        except OSError:
            return False

    def _spawn_mpv(self):
        """Start a fresh, idle mpv process listening on the IPC socket."""
        # Remove a stale socket file left behind by a previous crash.
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
        subprocess.Popen(
            [
                "mpv",
                "--idle=yes",
                "--no-video",
                "--no-terminal",
                "--input-ipc-server=" + self.socket_path,
                "--volume=%d" % self.default_volume,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait (up to ~5s) for the socket to become available.
        for _ in range(50):
            if self._can_connect():
                return
            time.sleep(0.1)

    def _connect(self):
        """Open the IPC socket and start the background reader thread."""
        for _ in range(50):
            try:
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._sock.connect(self.socket_path)
                break
            except OSError:
                self._sock = None
                time.sleep(0.1)
        else:
            raise RuntimeError("could not connect to mpv IPC socket")
        self._running = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # --- low level IPC --------------------------------------------------
    def _read_loop(self):
        """Background thread: dispatch responses and mpv events."""
        buf = b""
        while self._running:
            try:
                chunk = self._sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                self._dispatch(msg)
        # Reader stopped: mark the connection as dead so the TUI reconnects.
        self._running = False

    def _dispatch(self, msg):
        """Route an incoming IPC message to a waiter or an event handler."""
        if "request_id" in msg:
            pending = self._pending.pop(msg["request_id"], None)
            if pending:
                event, result = pending
                result.append(msg)
                event.set()
        elif msg.get("event") == "end-file" and msg.get("reason") == "eof":
            # A track finished on its own: advance in a separate thread so
            # the reader stays free to dispatch the loadfile response.
            threading.Thread(target=self._handle_eof, daemon=True).start()

    def command(self, *args, timeout=2.0):
        """Send an mpv command and wait for the reply.

        Returns the ``data`` field on success, or ``None`` on error.
        """
        if self._sock is None:
            return None
        with self._lock:
            self._req_id += 1
            req_id = self._req_id
        event = threading.Event()
        result = []
        self._pending[req_id] = (event, result)
        payload = json.dumps({"command": list(args), "request_id": req_id})
        try:
            with self._lock:
                self._sock.sendall(payload.encode("utf-8") + b"\n")
        except OSError:
            self._pending.pop(req_id, None)
            return None
        if not event.wait(timeout):
            self._pending.pop(req_id, None)
            return None
        msg = result[0]
        if msg.get("error") == "success":
            return msg.get("data")
        return None

    def get_property(self, name):
        return self.command("get_property", name)

    def set_property(self, name, value):
        return self.command("set_property", name, value)

    # --- queue / playback ----------------------------------------------
    def set_queue(self, tracks, index=0):
        """Replace the playback queue."""
        self.queue = list(tracks)
        self.index = index if self.queue else -1

    def current_track(self):
        """Return the track dict currently selected in the queue."""
        if 0 <= self.index < len(self.queue):
            return self.queue[self.index]
        return None

    def play_index(self, i):
        """Play the queue entry at index ``i``."""
        if not (0 <= i < len(self.queue)):
            return
        self.index = i
        self._play_track(self.queue[i])

    def _play_track(self, track):
        """Resolve (if needed) and load a track into mpv."""
        url = track["url_or_path"]
        if track.get("source") == "youtube" and self.resolver:
            resolved = self.resolver(track)
            if resolved:
                url = resolved
        self.command("loadfile", url, "replace")
        self.set_property("pause", False)
        if self.on_track_change:
            self.on_track_change(track)

    def _handle_eof(self):
        """Auto-advance when the current track ends naturally."""
        if self.loop_track:
            return  # mpv's loop-file handles repeats; eof will not fire
        nxt = self._next_index()
        if nxt is not None:
            self.play_index(nxt)

    def _next_index(self):
        """Compute the index of the next track, honouring shuffle/loop."""
        if not self.queue:
            return None
        if self.shuffle:
            return random.randrange(len(self.queue))
        if self.index + 1 < len(self.queue):
            return self.index + 1
        if self.loop_playlist:
            return 0
        return None

    def next(self):
        """Skip to the next track."""
        nxt = self._next_index()
        if nxt is not None:
            self.play_index(nxt)

    def prev(self):
        """Go back to the previous track."""
        if not self.queue:
            return
        if self.index - 1 >= 0:
            self.play_index(self.index - 1)
        elif self.loop_playlist:
            self.play_index(len(self.queue) - 1)

    def toggle_pause(self):
        """Toggle between play and pause."""
        paused = self.get_property("pause")
        self.set_property("pause", not bool(paused))

    def stop(self):
        """Stop playback (mpv stays alive and idle)."""
        self.command("stop")

    # --- volume / loop --------------------------------------------------
    def get_volume(self):
        vol = self.get_property("volume")
        return int(vol) if vol is not None else self.default_volume

    def set_volume(self, vol):
        vol = max(0, min(130, int(vol)))
        self.set_property("volume", vol)
        return vol

    def set_loop_track(self, on):
        """Enable or disable single-track looping via mpv's loop-file."""
        self.loop_track = on
        self.set_property("loop-file", "inf" if on else "no")

    # --- status ---------------------------------------------------------
    def status(self):
        """Return a snapshot of the playback state for the TUI."""
        if not self.connected():
            return {}
        return {
            "time_pos": self.get_property("time-pos"),
            "duration": self.get_property("duration"),
            "volume": self.get_property("volume"),
            "pause": self.get_property("pause"),
        }

    # --- shutdown -------------------------------------------------------
    def detach(self):
        """Disconnect the TUI but leave mpv playing in the background."""
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None

    def quit_all(self):
        """Stop playback and shut mpv down entirely."""
        self.command("quit")
        self.detach()

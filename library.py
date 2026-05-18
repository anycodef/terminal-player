"""Track, playlist and history persistence for mplayer-tui.

All data is stored as JSON under ``~/.config/mplayer-tui/``:

  library.json    list of track dicts
  playlists.json  mapping of {name: [track_id, ...]}
  history.json    list of {track_id, title, played_at}

Writes are atomic (write to a temp file then ``os.replace``) so an
interrupted save can never corrupt the on-disk data.
"""

import json
import os
import time
import uuid

from config import CONFIG_DIR

LIBRARY_FILE = os.path.join(CONFIG_DIR, "library.json")
PLAYLISTS_FILE = os.path.join(CONFIG_DIR, "playlists.json")
HISTORY_FILE = os.path.join(CONFIG_DIR, "history.json")

# Maximum number of entries kept in the play history.
HISTORY_LIMIT = 200


def _load(path, default):
    """Load JSON from ``path``, returning ``default`` on any failure."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _save(path, data):
    """Atomically write ``data`` as JSON to ``path``."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


class Library:
    """In-memory model of the music library backed by JSON files."""

    def __init__(self):
        self.tracks = _load(LIBRARY_FILE, [])
        self.playlists = _load(PLAYLISTS_FILE, {})
        self.history = _load(HISTORY_FILE, [])

    # --- tracks ---------------------------------------------------------
    def add_track(self, title, url_or_path, source):
        """Create a new track entry, persist it and return it.

        ``source`` is ``"youtube"`` for streams or ``"local"`` for files.
        """
        track = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "url_or_path": url_or_path,
            "source": source,
            "added_at": time.time(),
            "play_count": 0,
        }
        self.tracks.append(track)
        self.save_tracks()
        return track

    def delete_track(self, track_id):
        """Remove a track from the library and from every playlist."""
        self.tracks = [t for t in self.tracks if t["id"] != track_id]
        for name in self.playlists:
            self.playlists[name] = [
                tid for tid in self.playlists[name] if tid != track_id
            ]
        self.save_tracks()
        self.save_playlists()

    def get_track(self, track_id):
        """Return the track dict with ``track_id`` or ``None``."""
        for t in self.tracks:
            if t["id"] == track_id:
                return t
        return None

    def update_track(self, track_id, **fields):
        """Update fields of a track in place and persist the change."""
        track = self.get_track(track_id)
        if track:
            track.update(fields)
            self.save_tracks()
        return track

    # --- playlists ------------------------------------------------------
    def add_to_playlist(self, name, track_id):
        """Append a track to a (possibly new) named playlist."""
        self.playlists.setdefault(name, [])
        if track_id not in self.playlists[name]:
            self.playlists[name].append(track_id)
            self.save_playlists()

    def remove_playlist(self, name):
        """Delete an entire playlist."""
        self.playlists.pop(name, None)
        self.save_playlists()

    def playlist_tracks(self, name):
        """Return the resolved track dicts for the named playlist."""
        result = []
        for tid in self.playlists.get(name, []):
            track = self.get_track(tid)
            if track:
                result.append(track)
        return result

    # --- history --------------------------------------------------------
    def record_play(self, track):
        """Record a play event and bump the track's play_count."""
        self.history.insert(0, {
            "track_id": track["id"],
            "title": track["title"],
            "played_at": time.time(),
        })
        del self.history[HISTORY_LIMIT:]
        track["play_count"] = track.get("play_count", 0) + 1
        self.save_history()
        self.save_tracks()

    # --- persistence ----------------------------------------------------
    def save_tracks(self):
        _save(LIBRARY_FILE, self.tracks)

    def save_playlists(self):
        _save(PLAYLISTS_FILE, self.playlists)

    def save_history(self):
        _save(HISTORY_FILE, self.history)

"""curses TUI for mplayer-tui: panels, views and keybindings.

The screen is split into four panels:

  * top bar      - current track, state and loop status
  * progress bar - elapsed / total time and a visual bar
  * main panel   - one of four switchable views (Tab)
  * bottom bar   - keybinding hints, status messages and volume

The main loop redraws on a 500ms timeout so the progress bar and
status stay live even when the user is idle.
"""

import curses
import os
import threading
import time

from downloader import DownloaderError

# View identifiers.
VIEW_LIBRARY, VIEW_PLAYLISTS, VIEW_SEARCH, VIEW_HISTORY = range(4)
VIEW_NAMES = ["Library", "Playlists", "Search", "History"]


def fmt_time(seconds):
    """Format a number of seconds as ``M:SS``."""
    seconds = int(seconds or 0)
    return "%d:%02d" % (seconds // 60, seconds % 60)


class TUI:
    def __init__(self, stdscr, library, player, downloader, config):
        self.scr = stdscr
        self.lib = library
        self.player = player
        self.dl = downloader
        self.config = config

        self.view = VIEW_LIBRARY
        self.selection = 0          # index into the current row list
        self.scroll = 0             # first visible row
        self.search_query = ""
        self.search_active = False  # True while editing the search query
        self.expanded = set()       # names of expanded playlists

        self.message = "Welcome to mplayer-tui - press ? for help"
        self.message_time = time.time()
        self.running = True
        self.quit_audio = False     # set by 'Q' to also stop the audio

        self._status = {}           # latest playback snapshot
        self._last_reconnect = 0.0

        # Wire the player callbacks back into the TUI / library.
        self.player.resolver = self._resolve
        self.player.on_track_change = self.lib.record_play

    # --- player callbacks ----------------------------------------------
    def _resolve(self, track):
        """Resolve a YouTube track to a direct stream URL for mpv."""
        try:
            return self.dl.resolve_stream(track["url_or_path"])
        except DownloaderError as exc:
            self._notify("Error: " + str(exc))
            return None

    # --- main loop ------------------------------------------------------
    def run(self):
        """Run the curses event loop until the user quits."""
        curses.curs_set(0)
        self.scr.timeout(500)
        try:
            self._init_colors()
        except curses.error:
            pass
        while self.running:
            self._draw()
            try:
                key = self.scr.getch()
            except KeyboardInterrupt:
                break
            if key == -1:
                continue  # timeout: loop round to refresh the status
            self._handle_key(key)

    def _init_colors(self):
        """Initialise the colour pairs used across the interface."""
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)                   # accents
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)   # selection
        curses.init_pair(3, curses.COLOR_GREEN, -1)                  # playing
        curses.init_pair(4, curses.COLOR_YELLOW, -1)                 # messages

    # --- drawing --------------------------------------------------------
    def _addstr(self, y, x, text, attr=0):
        """Draw a string, clipped to the screen to avoid curses errors."""
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        text = text[: max(0, w - x - 1)]
        try:
            self.scr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def _draw(self):
        """Render a full frame."""
        # Reconnect to mpv in the background if the socket dropped.
        if not self.player.connected():
            now = time.time()
            if now - self._last_reconnect > 2:
                self._last_reconnect = now
                try:
                    self.player.start()
                except RuntimeError:
                    pass
        self._status = self.player.status()

        self.scr.erase()
        h, w = self.scr.getmaxyx()
        if h < 9 or w < 40:
            self._addstr(0, 0, "Terminal too small")
            self.scr.refresh()
            return
        self._draw_topbar(w)
        self._draw_progress(w)
        self._draw_main(h, w)
        self._draw_bottombar(h, w)
        self.scr.refresh()

    def _draw_topbar(self, w):
        track = self.player.current_track()
        title = track["title"] if track else "Nothing playing"
        artist = ""
        if track and track.get("source") == "youtube":
            artist = "  (stream)"
        loop = ""
        if self.player.loop_track:
            loop = "  loop:track"
        elif self.player.loop_playlist:
            loop = "  loop:playlist"
        if self.player.shuffle:
            loop += "  shuffle"
        line = " mplayer-tui   %s%s   [%s]%s" % (
            title, artist, self._playback_state(), loop)
        self._addstr(0, 0, line.ljust(w - 1),
                     curses.color_pair(1) | curses.A_BOLD)

    def _playback_state(self):
        if not self.player.connected():
            return "connecting..."
        if self._status.get("time_pos") is None:
            return "STOPPED"
        return "PAUSED" if self._status.get("pause") else "PLAYING"

    def _draw_progress(self, w):
        pos = self._status.get("time_pos") or 0
        dur = self._status.get("duration") or 0
        pct = (pos / dur) if dur else 0.0
        bar_w = max(10, w - 28)
        filled = int(bar_w * pct)
        bar = "#" * filled + "-" * (bar_w - filled)
        line = " %s [%s] %s %3d%%" % (
            fmt_time(pos), bar, fmt_time(dur), int(pct * 100))
        self._addstr(1, 0, line.ljust(w - 1), curses.color_pair(1))

    def _draw_main(self, h, w):
        # Row of view tabs.
        x = 1
        for i, name in enumerate(VIEW_NAMES):
            label = " %d:%s " % (i + 1, name)
            attr = curses.color_pair(2) if i == self.view else curses.color_pair(1)
            self._addstr(2, x, label, attr)
            x += len(label) + 1

        # Line 3: search bar for the search view, blank otherwise.
        if self.view == VIEW_SEARCH:
            cursor = "_" if self.search_active else ""
            bar = " Search: " + self.search_query + cursor
            self._addstr(3, 0, bar.ljust(w - 1), curses.color_pair(4))

        rows = self.build_rows()
        self._clamp(rows, h)
        main_h = max(1, h - 7)
        list_top = 4

        if not rows:
            self._addstr(list_top, 2, "(empty)", curses.A_DIM)
            return

        current = self.player.current_track()
        for i in range(self.scroll, min(len(rows), self.scroll + main_h)):
            row = rows[i]
            y = list_top + (i - self.scroll)
            text = row["text"]
            attr = 0
            track = row.get("track")
            if track and current and track["id"] == current["id"]:
                attr = curses.color_pair(3)
                text = ">" + text[1:]
            if i == self.selection:
                attr = curses.color_pair(2)
            self._addstr(y, 0, text.ljust(w - 1), attr)

    def _draw_bottombar(self, h, w):
        # Status message (auto-expires after a few seconds).
        if self.message and time.time() - self.message_time < 6:
            self._addstr(h - 3, 0, (" " + self.message).ljust(w - 1),
                         curses.color_pair(4))
        hints1 = (" Space play/pause  n/p next/prev  +/- volume  "
                  "Enter play  Tab switch view")
        hints2 = (" a add  d delete  A playlist  D download  "
                  "l loop  L loop-list  s shuffle  q quit  Q quit+stop")
        self._addstr(h - 2, 0, hints1.ljust(w - 1), curses.color_pair(1))
        self._addstr(h - 1, 0, hints2.ljust(w - 1), curses.color_pair(1))
        vol = self._status.get("volume")
        vol_txt = ("Vol: %d%% " % int(vol)) if vol is not None else "Vol: --  "
        self._addstr(h - 1, max(0, w - len(vol_txt) - 1), vol_txt,
                     curses.color_pair(1) | curses.A_BOLD)

    # --- row model ------------------------------------------------------
    def build_rows(self):
        """Build the selectable row list for the current view.

        Each row is a dict with ``text`` plus optional ``track``,
        ``queue`` (the list to load when played) and ``playlist``.
        """
        rows = []
        if self.view in (VIEW_LIBRARY, VIEW_SEARCH):
            tracks = self.lib.tracks
            if self.view == VIEW_SEARCH and self.search_query:
                q = self.search_query.lower()
                tracks = [t for t in tracks if q in t["title"].lower()]
            for t in tracks:
                tag = "local" if t["source"] == "local" else "stream"
                rows.append({
                    "text": "  [%-6s] %s" % (tag, t["title"]),
                    "track": t, "queue": tracks, "playlist": None,
                })
        elif self.view == VIEW_PLAYLISTS:
            for name in sorted(self.lib.playlists):
                tracks = self.lib.playlist_tracks(name)
                mark = "v" if name in self.expanded else ">"
                rows.append({
                    "text": "  %s %s (%d)" % (mark, name, len(tracks)),
                    "track": None, "queue": None, "playlist": name,
                })
                if name in self.expanded:
                    for t in tracks:
                        rows.append({
                            "text": "      %s" % t["title"],
                            "track": t, "queue": tracks, "playlist": name,
                        })
        elif self.view == VIEW_HISTORY:
            for h in self.lib.history:
                stamp = time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(h["played_at"]))
                rows.append({
                    "text": "  %s  %s" % (stamp, h["title"]),
                    "track": self.lib.get_track(h["track_id"]),
                    "queue": self.lib.tracks, "playlist": None,
                })
        return rows

    def _clamp(self, rows, h):
        """Keep the selection in range and scroll it into view."""
        n = len(rows)
        if n == 0:
            self.selection = 0
            self.scroll = 0
            return
        self.selection = max(0, min(self.selection, n - 1))
        main_h = max(1, h - 7)
        if self.selection < self.scroll:
            self.scroll = self.selection
        elif self.selection >= self.scroll + main_h:
            self.scroll = self.selection - main_h + 1

    # --- input handling -------------------------------------------------
    def _handle_key(self, key):
        if key == curses.KEY_RESIZE:
            return
        # While editing the search query, most keys edit text.
        if self.view == VIEW_SEARCH and self.search_active:
            if self._handle_search_key(key):
                return

        if key in (curses.KEY_UP, ord("k")):
            self.selection -= 1
        elif key in (curses.KEY_DOWN, ord("j")):
            self.selection += 1
        elif key == curses.KEY_PPAGE:
            self.selection -= 10
        elif key == curses.KEY_NPAGE:
            self.selection += 10
        elif key == 9:  # Tab
            self.view = (self.view + 1) % 4
            self.selection = 0
            self.scroll = 0
            self.search_active = (self.view == VIEW_SEARCH)
        elif key in (curses.KEY_ENTER, 10, 13):
            self._activate_selection()
        elif key == ord(" "):
            self.player.toggle_pause()
        elif key == ord("n"):
            self.player.next()
        elif key == ord("p"):
            self.player.prev()
        elif key in (ord("+"), ord("=")):
            self._change_volume(5)
        elif key in (ord("-"), ord("_")):
            self._change_volume(-5)
        elif key == ord("l"):
            self._toggle_loop_track()
        elif key == ord("L"):
            self._toggle_loop_playlist()
        elif key == ord("s"):
            self.player.shuffle = not self.player.shuffle
            self._notify("Shuffle %s" % self._onoff(self.player.shuffle))
        elif key == ord("a"):
            self._add_track()
        elif key == ord("d"):
            self._delete_track()
        elif key == ord("A"):
            self._add_to_playlist()
        elif key == ord("D"):
            self._download_track()
        elif key == ord("/") and self.view == VIEW_SEARCH:
            self.search_active = True
        elif key == ord("?"):
            self._notify("Keys: Space n p +/- l L s a d A D Enter Tab q Q")
        elif key == ord("q"):
            self.running = False
        elif key == ord("Q"):
            self.running = False
            self.quit_audio = True

    def _handle_search_key(self, key):
        """Edit the search query. Returns True if the key was consumed."""
        if key == 27:  # Esc leaves edit mode so global keys work again
            self.search_active = False
            return True
        # Navigation, Enter and Tab fall through to the global handler.
        if key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_NPAGE,
                   curses.KEY_PPAGE, curses.KEY_ENTER, 10, 13, 9,
                   curses.KEY_RESIZE):
            return False
        if key in (curses.KEY_BACKSPACE, 127, 8):
            self.search_query = self.search_query[:-1]
            self.selection = 0
            return True
        if 32 <= key <= 126:
            self.search_query += chr(key)
            self.selection = 0
            return True
        return True  # swallow anything else while editing

    # --- actions --------------------------------------------------------
    def _activate_selection(self):
        """Handle Enter: play a track or expand/collapse a playlist."""
        rows = self.build_rows()
        if not (0 <= self.selection < len(rows)):
            return
        row = rows[self.selection]
        if row.get("track") is None and row.get("playlist"):
            name = row["playlist"]
            self.expanded.discard(name) if name in self.expanded \
                else self.expanded.add(name)
            return
        track = row.get("track")
        if not track:
            self._notify("Track is no longer in the library")
            return
        queue = row.get("queue") or [track]
        idx = next((i for i, t in enumerate(queue)
                    if t["id"] == track["id"]), 0)
        if not self.player.connected():
            try:
                self.player.start()
            except RuntimeError:
                self._notify("mpv not reachable - try again")
                return
        self.player.set_queue(queue, idx)
        self.player.play_index(idx)
        self._notify("Playing: " + track["title"])

    def _selected_track(self):
        rows = self.build_rows()
        if 0 <= self.selection < len(rows):
            return rows[self.selection].get("track")
        return None

    def _change_volume(self, delta):
        new = self.player.set_volume(self.player.get_volume() + delta)
        self._notify("Volume: %d%%" % new)

    def _toggle_loop_track(self):
        on = not self.player.loop_track
        self.player.set_loop_track(on)
        if on:
            self.player.loop_playlist = False
        self._notify("Loop track %s" % self._onoff(on))

    def _toggle_loop_playlist(self):
        self.player.loop_playlist = not self.player.loop_playlist
        if self.player.loop_playlist:
            self.player.set_loop_track(False)
        self._notify("Loop playlist %s" % self._onoff(self.player.loop_playlist))

    def _add_track(self):
        """Prompt for a URL or local path and add it to the library."""
        value = self._prompt("Add URL or local path: ")
        if not value:
            return
        if value.startswith("http://") or value.startswith("https://"):
            self._notify("Fetching metadata...")
            self._draw()
            try:
                meta = self.dl.fetch_metadata(value)
            except DownloaderError as exc:
                self._notify("Error: " + str(exc))
                return
            self.lib.add_track(meta["title"], value, "youtube")
            self._notify("Added: " + meta["title"])
        else:
            path = os.path.expanduser(value)
            if not os.path.isfile(path):
                self._notify("File not found: " + path)
                return
            title = os.path.splitext(os.path.basename(path))[0]
            self.lib.add_track(title, path, "local")
            self._notify("Added: " + title)

    def _delete_track(self):
        track = self._selected_track()
        if not track:
            self._notify("No track selected")
            return
        self.lib.delete_track(track["id"])
        self._notify("Deleted: " + track["title"])

    def _add_to_playlist(self):
        track = self._selected_track()
        if not track:
            self._notify("No track selected")
            return
        name = self._prompt("Add to playlist (name): ")
        if not name:
            return
        self.lib.add_to_playlist(name, track["id"])
        self._notify("Added '%s' to playlist '%s'" % (track["title"], name))

    def _download_track(self):
        """Download the selected stream locally in a background thread."""
        track = self._selected_track()
        if not track:
            self._notify("No track selected")
            return
        if track["source"] == "local":
            self._notify("Track is already local")
            return
        url, tid, title = track["url_or_path"], track["id"], track["title"]
        self._notify("Downloading '%s' in background..." % title)

        def work():
            try:
                path = self.dl.download(url)
            except DownloaderError as exc:
                self._notify("Download failed: " + str(exc))
                return
            if path and os.path.isfile(path):
                self.lib.update_track(tid, url_or_path=path, source="local")
                self._notify("Downloaded: " + os.path.basename(path))
            else:
                self._notify("Download finished but file was not found")

        threading.Thread(target=work, daemon=True).start()

    # --- helpers --------------------------------------------------------
    def _prompt(self, label):
        """Read a line of text from the user at the bottom of the screen."""
        h, w = self.scr.getmaxyx()
        curses.curs_set(1)
        curses.echo()
        self.scr.timeout(-1)
        self.scr.move(h - 1, 0)
        self.scr.clrtoeol()
        self._addstr(h - 1, 0, label, curses.A_BOLD)
        self.scr.refresh()
        try:
            raw = self.scr.getstr(h - 1, min(len(label), w - 2), 512)
        except curses.error:
            raw = b""
        curses.noecho()
        curses.curs_set(0)
        self.scr.timeout(500)
        return raw.decode("utf-8", "replace").strip()

    def _notify(self, text):
        self.message = text
        self.message_time = time.time()

    @staticmethod
    def _onoff(value):
        return "on" if value else "off"

"""yt-dlp wrapper for stream resolution, metadata and downloads.

Every call shells out to the ``yt-dlp`` binary. Cookies are pulled from
the configured browser so that age-restricted or region-locked content
keeps working without manual cookie files.
"""

import datetime
import json
import os
import shutil
import subprocess
import threading


class DownloaderError(Exception):
    """Raised when a yt-dlp invocation fails."""


# yt-dlp swaps the characters it cannot put in a filename for look-alikes,
# so a title and the name it was saved under are compared through this.
_LOOKALIKES = str.maketrans({
    "\uff02": '"', "\uff1a": ":", "\uff0a": "*", "\uff1f": "?",
    "\uff1c": "<", "\uff1e": ">", "\uff5c": "|", "\u29f9": "\\",
    "\u29f8": "/",
})

# Partial downloads and yt-dlp's own bookkeeping files are not music.
_NOT_MUSIC = (".part", ".ytdl", ".temp", "")


def _loose(text):
    """Fold a title or a filename so the two can be compared."""
    return " ".join(text.translate(_LOOKALIKES).lower().split())


class Downloader:
    # yt-dlp releases are dated. YouTube changes its streaming endpoints
    # every few months, so a binary older than this silently breaks both
    # playback and downloads with 403s.
    STALE_DAYS = 60

    # Tag put on every progress line so it cannot be mistaken for the
    # filepath that --print writes to the same stream.
    PROGRESS_MARKER = "MPTUI "

    def __init__(self, browser="firefox", music_path="~/music"):
        self.browser = browser
        self.music_path = os.path.expanduser(music_path)

    def _cookie_args(self):
        """Build the --cookies-from-browser arguments, if configured."""
        if self.browser:
            return ["--cookies-from-browser", self.browser]
        return []

    def version(self):
        """Return the version string of the yt-dlp on PATH, or None."""
        try:
            out = subprocess.run(["yt-dlp", "--version"],
                                 capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None

    def stale_warning(self):
        """Return a warning if yt-dlp is missing or too old, else None."""
        version = self.version()
        if version is None:
            return "yt-dlp is not installed or failed to run"
        try:
            released = datetime.datetime.strptime(version[:10], "%Y.%m.%d")
        except ValueError:
            return None  # unexpected version format: nothing to judge
        age = (datetime.datetime.now() - released).days
        if age < self.STALE_DAYS:
            return None
        # Name the binary: a stale copy earlier on PATH (for example in
        # /usr/local/bin) shadows an up-to-date packaged one.
        return ("yt-dlp %s (%s) is %d days old - YouTube will reject "
                "streams and downloads; update it"
                % (version, shutil.which("yt-dlp") or "yt-dlp", age))

    def existing_file(self, title):
        """Return the file in ``music_path`` saved for ``title``, if any.

        A download whose interface died never made it back into the
        library, so the track still claims to be a stream while its file
        sits on disk. Matching by name is what is left to go on.
        """
        wanted = _loose(title)
        try:
            names = os.listdir(self.music_path)
        except OSError:
            return None
        matches = [name for name in names
                   if os.path.splitext(name)[1].lower() not in _NOT_MUSIC
                   and _loose(os.path.splitext(name)[0]) == wanted]
        if not matches:
            return None
        # Prefer the mp3 this downloader produces: a run interrupted
        # between the conversion and the cleanup leaves the source
        # (a .webm, say) sitting next to it.
        matches.sort(key=lambda name: (not name.lower().endswith(".mp3"), name))
        return os.path.join(self.music_path, matches[0])

    def fetch_metadata(self, url):
        """Return ``{"title", "duration"}`` for a URL using ``yt-dlp -J``."""
        cmd = ["yt-dlp", "-J", "--no-playlist", "--skip-download"]
        cmd += self._cookie_args()
        cmd.append(url)
        out = self._run(cmd, timeout=60)
        try:
            data = json.loads(out.stdout)
        except ValueError:
            raise DownloaderError("could not parse yt-dlp metadata")
        return {
            "title": data.get("title", url),
            "duration": data.get("duration"),
        }

    def resolve_stream(self, url):
        """Return a direct audio stream URL playable by mpv."""
        cmd = ["yt-dlp", "-g", "-f", "bestaudio/best", "--no-playlist"]
        cmd += self._cookie_args()
        cmd.append(url)
        out = self._run(cmd, timeout=60)
        urls = [line for line in out.stdout.splitlines() if line.strip()]
        if not urls:
            raise DownloaderError("yt-dlp returned no stream URL")
        return urls[0]

    def download(self, url, on_progress=None):
        """Download audio as mp3 into ``music_path``; return the file path.

        ``on_progress`` is called with the percentage downloaded so far.
        The mp3 conversion that follows reports no progress of its own,
        so it stays at 100% until the file lands.
        """
        os.makedirs(self.music_path, exist_ok=True)
        template = os.path.join(self.music_path, "%(title)s.%(ext)s")
        cmd = [
            "yt-dlp", "-x", "--audio-format", "mp3", "--no-playlist",
            "-o", template, "--print", "after_move:filepath",
            # --print implies --quiet, so the progress has to be asked
            # for; --newline puts each update on a line of its own.
            "--progress", "--newline", "--progress-template",
            "download:" + self.PROGRESS_MARKER + "%(progress._percent_str)s",
        ]
        cmd += self._cookie_args()
        cmd.append(url)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
        except OSError:
            raise DownloaderError("yt-dlp is not installed")
        # Drain stderr in a thread: a full pipe would otherwise block
        # yt-dlp half way through the download.
        errors = []
        drain = threading.Thread(target=lambda: errors.extend(proc.stderr),
                                 daemon=True)
        drain.start()
        printed = []
        for line in proc.stdout:
            line = line.strip()
            if line.startswith(self.PROGRESS_MARKER):
                percent = self._parse_percent(line[len(self.PROGRESS_MARKER):])
                if percent is not None and on_progress:
                    on_progress(percent)
            elif line:
                printed.append(line)
        if proc.wait() != 0:
            drain.join(timeout=5)
            raise DownloaderError(self._explain("".join(errors)))
        return printed[-1] if printed else None

    @staticmethod
    def _parse_percent(text):
        """Parse yt-dlp's ``_percent_str`` ("  42.1%") into a float."""
        try:
            return float(text.strip().rstrip("%"))
        except ValueError:
            return None

    def _run(self, cmd, timeout):
        """Run a yt-dlp command, raising DownloaderError on failure."""
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise DownloaderError("yt-dlp is not installed")
        except subprocess.TimeoutExpired:
            raise DownloaderError("yt-dlp timed out")
        if out.returncode != 0:
            raise DownloaderError(self._explain(out.stderr))
        return out

    @staticmethod
    def _explain(stderr):
        """Turn common yt-dlp failures into actionable messages."""
        text = (stderr or "").lower()
        if "403" in text or "forbidden" in text:
            return ("403 Forbidden - YouTube rejected the request. "
                    "Usually an outdated yt-dlp: update it and retry.")
        if "sign in" in text or "bot" in text or "confirm you" in text:
            return ("Bot detection - set the correct browser for cookies "
                    "in config.json (firefox/chrome/chromium).")
        lines = (stderr or "").strip().splitlines()
        return lines[-1] if lines else "yt-dlp failed"

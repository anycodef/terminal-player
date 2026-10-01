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


class DownloaderError(Exception):
    """Raised when a yt-dlp invocation fails."""


class Downloader:
    # yt-dlp releases are dated. YouTube changes its streaming endpoints
    # every few months, so a binary older than this silently breaks both
    # playback and downloads with 403s.
    STALE_DAYS = 60

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

    def download(self, url):
        """Download audio as mp3 into ``music_path``; return the file path."""
        os.makedirs(self.music_path, exist_ok=True)
        template = os.path.join(self.music_path, "%(title)s.%(ext)s")
        cmd = [
            "yt-dlp", "-x", "--audio-format", "mp3", "--no-playlist",
            "-o", template, "--print", "after_move:filepath",
        ]
        cmd += self._cookie_args()
        cmd.append(url)
        out = self._run(cmd, timeout=600)
        lines = [line for line in out.stdout.splitlines() if line.strip()]
        return lines[-1] if lines else None

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

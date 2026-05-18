"""yt-dlp wrapper for stream resolution, metadata and downloads.

Every call shells out to the ``yt-dlp`` binary. Cookies are pulled from
the configured browser so that age-restricted or region-locked content
keeps working without manual cookie files.
"""

import json
import os
import subprocess


class DownloaderError(Exception):
    """Raised when a yt-dlp invocation fails."""


class Downloader:
    def __init__(self, browser="firefox", music_path="~/music"):
        self.browser = browser
        self.music_path = os.path.expanduser(music_path)

    def _cookie_args(self):
        """Build the --cookies-from-browser arguments, if configured."""
        if self.browser:
            return ["--cookies-from-browser", self.browser]
        return []

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
            return ("403 Forbidden - YouTube blocked the request. "
                    "Check the 'browser' field in config.json.")
        if "sign in" in text or "bot" in text or "confirm you" in text:
            return ("Bot detection - set the correct browser for cookies "
                    "in config.json (firefox/chrome/chromium).")
        lines = (stderr or "").strip().splitlines()
        return lines[-1] if lines else "yt-dlp failed"

# mplayer-tui

A terminal music player for Arch Linux. It streams audio from YouTube
(and any site `yt-dlp` supports), manages a local library and playlists,
and keeps playing in the background after you close the interface.

Built on Python's `curses`, with `mpv` as the audio backend (controlled
over a JSON IPC socket), `yt-dlp` for stream resolution, and `tmux` for
background session management.

## Demo

```
 mplayer-tui   Daft Punk - Get Lucky  (stream)   [PLAYING]  loop:playlist
 1:23 [###############-----------------------] 4:08  33%

  1:Library   2:Playlists   3:Search   4:History

  [stream] Daft Punk - Get Lucky
> [stream] Tame Impala - Let It Happen
  [local ] Boards of Canada - Roygbiv
  [stream] Aphex Twin - Avril 14th
  [local ] Radiohead - Weird Fishes

 Welcome to mplayer-tui - press ? for help
 Space play/pause  n/p next/prev  +/- volume  Enter play  Tab switch view
 a add  d delete  A playlist  D download  l loop  L loop-list  s shuffle      Vol: 80%
```

## Requirements

- `python3` (with the standard-library `curses` module)
- `mpv`
- `yt-dlp`
- `tmux`

Install them on Arch Linux with:

```sh
sudo pacman -S python mpv yt-dlp tmux
```

## Installation

```sh
git clone <repository-url> mplayer-tui
cd mplayer-tui
./install.sh
```

The installer checks dependencies, creates `~/.config/mplayer-tui/`
with a default `config.json`, and installs the `music` command into
`/usr/local/bin` (it may prompt for `sudo`).

## Configuration

Settings live in `~/.config/mplayer-tui/config.json`:

| Field            | Description                                                      | Default        |
|------------------|------------------------------------------------------------------|----------------|
| `browser`        | Browser used for `--cookies-from-browser` (`firefox`/`chrome`/`chromium`). Set to `""` to disable cookies. | `firefox`      |
| `music_path`     | Directory where downloaded tracks are saved.                     | `~/music`      |
| `default_volume` | Volume mpv starts at, 0-130.                                     | `80`           |
| `theme`          | Color theme name.                                                | `default`      |

Other data files in the same directory:

- `library.json` — every saved track (id, title, source, play count)
- `playlists.json` — named playlists as ordered lists of track ids
- `history.json` — the last 200 played tracks with timestamps

## Keybindings

| Key       | Action                                                  |
|-----------|---------------------------------------------------------|
| `Space`   | Play / pause                                            |
| `n` / `p` | Next / previous track                                   |
| `+` / `-` | Volume up / down (5% steps)                             |
| `l`       | Toggle single-track loop                                |
| `L`       | Toggle playlist loop                                    |
| `s`       | Toggle shuffle                                          |
| `Enter`   | Play the selected track (or expand a playlist)          |
| `a`       | Add a URL or local file path to the library             |
| `d`       | Delete the selected track from the library              |
| `A`       | Add the selected track to a playlist                    |
| `D`       | Download the selected stream locally                    |
| `Tab`     | Switch between Library / Playlists / Search / History   |
| `↑` / `↓` | Move the selection (also `k` / `j`)                     |
| `?`       | Show a keybinding reminder                              |
| `q`       | Quit the interface (audio keeps playing)                |
| `Q`       | Quit the interface and stop the audio                   |

In the **Search** view, just start typing to filter the library in
real time. Press `Esc` to leave edit mode (so the playback keys work
again) and `/` to resume typing.

## Usage

Start the player:

```sh
music
```

Stop everything (interface and audio):

```sh
music stop
```

### Adding songs

Press `a` and paste a YouTube URL — the title is fetched automatically
and the track is saved as a stream. You can also enter a local file
path to add an existing file.

### Creating playlists

Select a track, press `A`, and type a playlist name (new or existing).
Switch to the **Playlists** view with `Tab`, press `Enter` on a
playlist to expand it, and `Enter` on a track to play the whole
playlist from that point.

### Running in the background

Audio runs as a separate `mpv` process, so playback survives the
interface closing:

- Press `q` to close the interface — the music keeps playing.
- Or press `Ctrl+B` then `D` to detach the `tmux` session, which also
  keeps the interface state intact.

### Reattaching

Run `music` again. If a background session exists it reattaches to it;
otherwise it reconnects to the running `mpv` and shows a fresh
interface.

### Downloading for offline use

Select a stream track and press `D`. It is downloaded as an mp3 into
`music_path` and the library entry is switched from `stream` to
`local`.

## Troubleshooting

**403 Forbidden / "Sign in to confirm" errors**
YouTube is blocking anonymous requests. mplayer-tui passes browser
cookies to `yt-dlp` via `--cookies-from-browser`. Make sure the
`browser` field in `config.json` matches a browser you are logged into
YouTube with, and that the browser is closed or not locking its cookie
database.

**Cookie setup**
Supported values for `browser` are `firefox`, `chrome` and `chromium`.
Log into YouTube in that browser once; `yt-dlp` reads its cookie store
directly. Set `browser` to `""` to disable cookies entirely.

**mpv socket issues**
If the interface is stuck on `connecting...`, a stale socket may exist
at `/tmp/mpv-music.sock`. Run `music stop` to clean everything up, then
start again. mplayer-tui also removes stale sockets automatically on
launch.

**tmux not installed**
mplayer-tui still runs without `tmux`, but without background session
management — closing the terminal stops the interface (audio still
continues until the `mpv` process is stopped).

## License

Released under the MIT License.

```
MIT License

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

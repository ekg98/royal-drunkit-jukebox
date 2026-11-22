# The Royal Drunkit Jukebox

Version 0.4.1 (see [CHANGELOG.md](CHANGELOG.md); `python3 run.py --version` shows the installed one).

A free, TouchTunes-style jukebox you run on your own computer or a Raspberry Pi. Guests pick songs from a phone or tablet
web page; the host controls credits, free play and party modes.

**Status:** The library scanner, queue, credits, voting, party modes, host panel, the neon web page, real playback
(through mpv, to the computer's audio output such as the headphone jack) and "Tidy my library" (look up missing cover
art, years and genres online) are built. Not yet built: several Bluetooth speakers at once.

## What you need

- Python 3.11 or newer
- ffmpeg (provides `ffprobe` too). Windows: `winget install ffmpeg`. Raspberry Pi / Debian: `sudo apt install ffmpeg`. Mac: `brew install ffmpeg`.
- mpv, which plays the sound: `sudo apt install mpv` (without it the jukebox runs in a silent, simulated mode)
- Flask: `pip install -r requirements.txt` (or `sudo apt install python3-flask`)

## Try it in two minutes

```
python tools/make_sample_music.py music-demo --count 120
python run.py --music music-demo
```

Open the address it prints (`http://localhost:8080`, or the Wi-Fi address on a phone). Host PIN is `1234` until you change it.

## Sound

The jukebox plays through the computer's normal audio output. For the headphone jack / line out, make sure that output
is selected in the desktop's sound settings (or the Raspberry Pi's volume menu), then start the jukebox.
To pick an output yourself:

```
python3 run.py --list-audio-devices          # prints the outputs mpv can see
python3 run.py --music ~/Music --audio-device "alsa/plughw:CARD=PCH,DEV=0"
```

You can also set `audio_device` in `config.toml`. The Host panel's volume slider is mpv's volume; keep your amplifier or
speakers at a comfortable level and use the slider for fine control. `--simulate` turns sound off for testing.

## Use your own music

```
python run.py --music "D:\Music" --pin 4829
```

or copy `config.example.toml` to `config.toml` and edit it. Formats: mp3, flac, m4a/aac, ogg, opus, wav, wma and more.
Song info comes from the files' tags (title, artist, album, genre, year); missing tags fall back to the file name.
Cover art comes from embedded pictures, or `cover.jpg` / `folder.jpg` in the album folder.
After adding music, open the Host panel and press **Rescan music folder** (the first scan of thousands of songs takes a
few minutes; later scans only read new or changed files).

Every option is listed in [All the switches](#all-the-switches) below.

## All the switches

There are four places to change how the jukebox behaves. Later ones win over earlier ones where they overlap.

### 1. Command-line options (`python3 run.py ...`)

| Option | What it does | Default |
|---|---|---|
| `--music FOLDER` | Folder that holds your music (sub-folders are scanned) | `music` next to `run.py` |
| `--data FOLDER` | Where the catalog, cover art and saved Host settings are kept | `data` next to `run.py` |
| `--port N` | Web port | `8080` |
| `--host ADDRESS` | Network address to listen on (`127.0.0.1` = this computer only) | `0.0.0.0` (everything on your network) |
| `--pin PIN` | PIN for the Host panel | `1234` |
| `--audio-device NAME` | Audio output for mpv | the system's default output |
| `--list-audio-devices` | Print the outputs mpv can use, then exit | |
| `--simulate` | No sound, pretend to play (for testing) | off |
| `--song-seconds N` | Pretend every song lasts N seconds (turns on `--simulate`) | real length |
| `--no-scan` | Skip scanning the music folder at startup | scan on |
| `--config FILE` | Use this settings file | `config.toml` next to `run.py` |
| `--version` | Print the version, then exit | |
| `--help` | Show the short help, then exit | |

### 2. `config.toml` (copy `config.example.toml` and edit; a command-line option wins over it)

| Setting | What it does | Default |
|---|---|---|
| `music_dir` | Same as `--music` | `"music"` |
| `data_dir` | Same as `--data` | `"data"` |
| `port` | Same as `--port` | `8080` |
| `host` | Same as `--host` | `"0.0.0.0"` |
| `host_pin` | Same as `--pin`. Change it. | `"1234"` |
| `audio_device` | Same as `--audio-device` | system default |
| `simulate` | `true` = no sound (same as `--simulate`) | `false` |
| `contact` | Your email or web page, sent to MusicBrainz by "Tidy my library" as a courtesy | empty |

Relative folders in `config.toml` are relative to the file's own folder.

### 3. Host panel (unlock with the PIN; saved in the data folder, so every screen shares them)

| Setting | Choices | Default |
|---|---|---|
| Master volume | 0 to 100 (mpv's volume) | 75 |
| Guest play | Credits, or Free play | Credits |
| Party mode | Anything goes, a decade night, or a genre night (only the decades and genres your library has) | Anything goes |
| Auto-fill when the queue is empty | on / off | on |
| Most credits a guest can hold | 3, 5, 8, 10 | 5 |
| A new credit arrives every | 1, 2, 5, 10 minutes | 2 minutes |
| Votes needed to skip | 2 to 6 | 3 |

Two more settings are not in the panel because they rarely change. Stop the jukebox, edit (or create) `settings.json` in the data
folder, and start it again: `start_credits` (credits a new guest starts with, 0 to 20, default 3) and `queue_max`
(songs guests can queue, 1 to 50, default 10).

| Button | What it does |
|---|---|
| Pause / Play, Skip now, Clear queue | Control what is playing and the queue (host songs cost no credits) |
| Rescan music folder | Reads new or changed files (the first scan of thousands of songs takes a few minutes) |
| Tidy my library / Stop tidying | Looks up missing cover art, years and genres online; see [Tidy my library](#tidy-my-library) |
| Never play this song | Bans the song that is playing right now and moves on |
| Show never-play list | Shows the banned songs so you can allow them again; see [Never-play list](#never-play-list) |
| Preview idle screen | Shows the idle screen right now |
| Lock host | Ends the host session on this screen |

### 4. Per screen (remembered by that phone, tablet or TV's browser; other screens are not affected)

| Setting | Choices | Default |
|---|---|---|
| Show the idle screen after | Never, 1, 2 or 5 minutes without a touch | 2 minutes |
| Idle screen style | Mixed, Floating covers, Cover wall, Spotlight, Spinning record, Coming up, Neon horizon, Neon cubes, Neon dream, Starfield | Mixed |
| TV view background | Mixed, Glow only, Neon horizon, Neon cubes, Neon dream, Starfield | Mixed |
| Visualizer style | Mixed, Bars, Mirror, Wave, Dots | Mixed |

"Mixed" rotates through the styles. Screens that ask for reduced motion get a calm version automatically.

### Fixed rules (not switches)

Add a song = 1 credit, Play next = 2. A song cannot be queued twice. A guest's phone is remembered by a random id in
its browser. The host session lasts 12 hours. Five wrong PINs lock the Host panel for one minute.

## Tidy my library

Host panel, **Tidy my library**. It looks up what your files are missing, using MusicBrainz and the Cover Art Archive
(both free, no account). It needs the internet only while it runs.

- Fills in **cover art, year and genre**. Your music files are never changed; results go in the jukebox's own data folder.
- Only blanks are filled. A cover, year or genre your files already have is never replaced.
- Looks up each album once (not each song). A result is used only if the album name and the artist both match, so
  you get a missing cover rather than a wrong one. Songs with no album tag are looked up by title and artist.
- It goes about one album every 3 seconds (MusicBrainz asks for no more than one request per second), so 300 albums take
  about 15 minutes. You can leave the page; use **Stop tidying** to end it early. The jukebox keeps playing.
- Albums that were not found are not asked about again for 30 days. Fix the tags or add a `cover.jpg`, then rescan.
- A song's `Unknown Artist` or missing title cannot be looked up. Fix those tags in the file (Asunder or Kid3 can do it).
- Optional: put `contact = "you@example.com"` in `config.toml`; it is sent to MusicBrainz as a courtesy.

## Never-play list

Keep songs (or whole albums) from ever playing, for example the one nobody wants to hear at a party.

- Unlock the Host panel, open any song, and choose **Never play this song** or **Never play this whole album**.
  While a song is playing, the Host panel also has **Never play this song**; it bans the song and moves on.
- Banned songs cannot be picked by guests or by auto-fill, and are pulled out of the queue. Guests do not see them at all.
- The host still sees them dimmed with a NEVER PLAY tag. **Show never-play list** in the Host panel lists them;
  open one and choose **Allow this song again**.
- The list is kept in the jukebox's data folder and survives rescans and restarts. If you move or rename a song file,
  the jukebox sees it as a new song, so ban it again.

## How it behaves

- Guests get 3 credits (max 5, one new credit every 2 minutes). Add = 1 credit, Play next = 2. Host can switch to **free play**.
- A guest's phone is remembered by a random id kept in the browser; the host's session lasts 12 hours.
- Queue holds 10 songs, no duplicates, 3 votes skip the current song. When the queue is empty, auto-fill picks songs
  that fit the party mode (any decade or genre in your library) and avoids recent repeats.
- Settings changed in the Host panel are saved in the data folder.
- Wrong host PINs: 5 tries, then a one-minute lockout.
- Anyone on your Wi-Fi can open the page. Do not expose it to the internet.

## Tests

```
python -m unittest discover -s tests -t .
```

(117 tests; they need ffmpeg because they build real audio files. The mpv player is tested against a stand-in mpv.)

## Layout

- `jukebox/` — config, tag reader, library, engine (all the rules), players (mpv and simulated), web server, `tidy.py` (online lookups)
- `static/index.html` — the neon page. If it cannot reach a server it runs as a demo with made-up songs.
- `tools/make_sample_music.py` — fake songs for trying things out
- `CHANGELOG.md` — what changed in each version

The page loads two fonts from Google Fonts; with no internet it falls back to system fonts.
The built-in web server is Flask's development server, fine for a basement on a home network.

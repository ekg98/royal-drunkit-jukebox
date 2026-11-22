# The Royal Drunkit Jukebox

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

Options: `--port`, `--host`, `--data`, `--no-scan`, `--song-seconds N` (make every simulated song N seconds, handy for testing).

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

(104 tests; they need ffmpeg because they build real audio files. The mpv player is tested against a stand-in mpv.)

## Layout

- `jukebox/` — config, tag reader, library, engine (all the rules), players (mpv and simulated), web server, `tidy.py` (online lookups)
- `static/index.html` — the neon page. If it cannot reach a server it runs as a demo with made-up songs.
- `tools/make_sample_music.py` — fake songs for trying things out

The page loads two fonts from Google Fonts; with no internet it falls back to system fonts.
The built-in web server is Flask's development server, fine for a basement on a home network.

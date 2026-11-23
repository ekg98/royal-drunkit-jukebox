# Changelog

The version number lives in one place, `jukebox/__init__.py`, and is shown by `python3 run.py --version`, in the
text the jukebox prints when it starts, and at the bottom of the Host panel. The page (`static/index.html`) carries
its own copy of the number; if the page and the jukebox ever differ, the Host panel says so. A test checks that they
match before a release.

Versions are `major.minor.patch`: a new feature raises the middle number, a fix or documentation change the last.
Dates are when the version was built.

## 0.5.2 - 2026-11-22
- With *Guests can rate songs and see ratings* off, guests no longer get the thumbs buttons and the jukebox refuses
  their votes (before, they could still vote without seeing totals). The host keeps the thumbs.

## 0.5.1 - 2026-11-22
- The demo preview page now has the same Host switches and buttons as the real jukebox (guest ratings, never-play list),
  so what you see in a preview matches the real thing.
- **Top rated** is hidden everywhere, including for the host, while *Guests can see ratings* is off.

## 0.5.0 - 2026-11-22
- **Guest ratings.** Thumbs up and thumbs down on the now-playing card, one vote per guest per song.
- Two new Host switches: *Guests' thumbs change what auto-fill picks* and *Guests can see ratings*.
- **Top rated** sort and Home row; **Show most disliked** and **Clear all ratings** in the Host panel.
  Ratings are kept across restarts.

## 0.4.1 - 2026-11-22
- Version number shown by `--version`, at startup and in the Host panel, with a warning if the page and the jukebox differ.
- README: a table of every command-line option, config.toml setting, Host panel setting and per-screen setting.
- This changelog.

## 0.4.0 - 2026-11-22
- **Never-play list.** The host can keep a song, or a whole album, from ever playing. Guests cannot see or pick banned
  songs, auto-fill skips them, and they are pulled out of the queue. The host can bring them back. Survives rescans
  and restarts; older catalogs are upgraded automatically.

## 0.3.0 - 2026-11-22
- **Tidy my library.** Looks up missing cover art, years and genres from MusicBrainz and the Cover Art Archive.
  Never changes your music files, never replaces what the files already say, and looks each album up once.
- Optional `contact` setting for MusicBrainz.

## 0.2.0 - 2026-11-21
- **Real sound** through mpv (headphone jack or any audio output), with `--audio-device`, `--list-audio-devices`
  and `--simulate`. The jukebox falls back to a silent simulated player if mpv is missing.
- Covers added to a folder after the first scan are found on the next rescan.
- More variety on the screens: idle screens (floating covers, cover wall, spotlight, spinning record, coming up,
  neon horizon, neon cubes, **neon dream** with the floating 25-cent quarter slot, starfield), TV backgrounds,
  four visualizer styles, and a glow that follows the album art. Chosen per screen in the Host panel.
- Host unlock button works in sandboxed previews.

## 0.1.0 - 2026-11-20
- First working version: library scanner (mp3, flac, m4a, ogg, opus, wav and more), queue, credits and free play,
  skip votes, party modes by decade and genre, auto-fill, Host panel with PIN, the neon web page for phones,
  tablets and TV, and a silent simulated player.

# iPod SSH Sync

Upload music directly to a jailbroken iPod's music library over SSH.

This script connects to a jailbroken iPod via SSH/SFTP, uploads audio files
that are not already on the device, and updates the iPod's iTunesDB binary
database so the new tracks appear in the Music app — no iTunes required.

## Requirements

- Python 3.8+
- A **jailbroken** iPod (Classic, Nano, Touch, etc.) with **OpenSSH** installed
  (available from Cydia)
- The iPod and your computer must be on the same network

Install the Python dependencies:

```bash
pip install -r requirements.txt
```

## Usage

```bash
python ipod_sync.py --host <IPOD_IP> --music-dir <LOCAL_MUSIC_DIR>
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | *(required)* | iPod IP address or hostname |
| `--port` | `22` | SSH port |
| `--user` | `root` | SSH username |
| `--password` | `alpine` | SSH password (default jailbreak password) |
| `--music-dir` | *(required)* | Local directory containing music files |
| `-v` / `--verbose` | off | Enable debug logging |

### Example

```bash
python ipod_sync.py --host 192.168.1.42 --music-dir ~/Music/ToSync -v
```

## Supported formats

`.mp3`, `.m4a`, `.aac`, `.wav`, `.aiff`

## How it works

1. Scans your local music directory for supported audio files.
2. Connects to the iPod over SSH and checks which files already exist.
3. Uploads new files into the iPod's `iTunes_Control/Music/F00–F49` folders.
4. Reads audio metadata (title, artist, album, etc.) from each file using
   [mutagen](https://mutagen.readthedocs.io/).
5. Parses the existing `iTunesDB` binary database (or creates a new one).
6. Adds track entries with the correct metadata and file paths.
7. Writes the updated database back to the iPod.

## Running tests

```bash
python -m unittest discover tests -v
```

## Project structure

```
ipod_sync.py       # Main script – SSH connection, upload, sync logic
itunesdb.py        # iTunesDB binary format parser and writer
requirements.txt   # Python dependencies
tests/             # Unit tests
```

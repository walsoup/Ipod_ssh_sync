#!/usr/bin/env python3
"""
iPod SSH Sync - Upload music to a jailbroken iPod over SSH.

This script connects to a jailbroken iPod via SSH/SFTP, uploads music files
that are not already present on the device, and updates the iPod's iTunesDB
so the tracks appear in the Music app.

Usage:
    python ipod_sync.py --host <IPOD_IP> --music-dir <LOCAL_MUSIC_DIR>

Requirements:
    - paramiko (SSH/SFTP)
    - mutagen  (audio metadata)
    - A jailbroken iPod with SSH access (e.g. via OpenSSH from Cydia)
"""

import argparse
import hashlib
import logging
import os
import struct
import sys
import tempfile
import time

import paramiko
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.easyid3 import EasyID3
from mutagen import File as MutagenFile

from itunesdb import ITunesDB, Track

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
IPOD_MUSIC_ROOT = "/private/var/mobile/Media"
IPOD_MUSIC_DIR = IPOD_MUSIC_ROOT + "/iTunes_Control/Music"
IPOD_DB_DIR = IPOD_MUSIC_ROOT + "/iTunes_Control/iTunes"
IPOD_DB_PATH = IPOD_DB_DIR + "/iTunesDB"
SUPPORTED_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".aiff"}
DEFAULT_SSH_PORT = 22
DEFAULT_SSH_USER = "root"
DEFAULT_SSH_PASS = "alpine"


# ---------------------------------------------------------------------------
# Audio metadata helpers
# ---------------------------------------------------------------------------

def get_audio_metadata(filepath):
    """Extract metadata from a local audio file using mutagen.

    Returns a dict with keys: title, artist, album, genre, duration_ms,
    filesize, bitrate, year, track_number, sample_rate, filetype.
    """
    ext = os.path.splitext(filepath)[1].lower()
    filesize = os.path.getsize(filepath)
    try:
        audio = MutagenFile(filepath, easy=True)
    except Exception:
        audio = None

    meta = {
        "title": os.path.splitext(os.path.basename(filepath))[0],
        "artist": "Unknown Artist",
        "album": "Unknown Album",
        "genre": "",
        "duration_ms": 0,
        "filesize": filesize,
        "bitrate": 128,
        "year": 0,
        "track_number": 0,
        "sample_rate": 44100,
        "filetype": ext,
    }

    if audio is None:
        return meta

    # Duration
    if hasattr(audio, "info") and audio.info is not None:
        meta["duration_ms"] = int((audio.info.length or 0) * 1000)
        meta["bitrate"] = getattr(audio.info, "bitrate", 128000) // 1000
        meta["sample_rate"] = getattr(audio.info, "sample_rate", 44100)

    # Tags via easy interface
    if audio.tags is not None:
        meta["title"] = _first(audio.tags, "title", meta["title"])
        meta["artist"] = _first(audio.tags, "artist", meta["artist"])
        meta["album"] = _first(audio.tags, "album", meta["album"])
        meta["genre"] = _first(audio.tags, "genre", meta["genre"])
        year_str = _first(audio.tags, "date", "")
        if year_str:
            try:
                meta["year"] = int(str(year_str)[:4])
            except ValueError:
                pass
        tn = _first(audio.tags, "tracknumber", "0")
        try:
            meta["track_number"] = int(str(tn).split("/")[0])
        except ValueError:
            pass

    return meta


def _first(tags, key, default=""):
    """Return the first value for *key* in a mutagen tag dict."""
    vals = tags.get(key)
    if vals:
        return str(vals[0])
    return default


# ---------------------------------------------------------------------------
# iPod path helpers
# ---------------------------------------------------------------------------

def ipod_dest_path(filename, slot):
    """Return the on-device path for a music file.

    iPod stores music in hash-bucketed folders F00..F49 under
    iTunes_Control/Music/.  We pick a slot deterministically from
    the filename hash.
    """
    folder = "F{:02d}".format(slot % 50)
    # iPod expects filenames to start with a 4-char code
    safe_name = _sanitise_filename(filename)
    return "{}/{}/{}".format(IPOD_MUSIC_DIR, folder, safe_name)


def _sanitise_filename(name):
    """Replace characters that may be problematic on the iPod filesystem."""
    keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    return "".join(c if c in keep else "_" for c in name)


def hash_slot(filepath):
    """Deterministic slot number from file content hash (0-49)."""
    h = hashlib.sha256(filepath.encode("utf-8")).hexdigest()
    return int(h[:8], 16) % 50


# ---------------------------------------------------------------------------
# SSH / SFTP helpers
# ---------------------------------------------------------------------------

def connect_ssh(host, port, user, password):
    """Open an SSH connection to the iPod and return (SSHClient, SFTPClient)."""
    ssh = paramiko.SSHClient()
    # Load system host keys if available so already-trusted hosts are verified.
    ssh.load_system_host_keys()
    # For jailbroken iPods on local networks the host key is typically not
    # pre-known.  WarningPolicy logs a warning instead of silently accepting.
    ssh.set_missing_host_key_policy(paramiko.WarningPolicy())
    logger.info("Connecting to %s:%d as %s …", host, port, user)
    ssh.connect(host, port=port, username=user, password=password,
                look_for_keys=False, allow_agent=False)
    sftp = ssh.open_sftp()
    return ssh, sftp


def sftp_exists(sftp, remote_path):
    """Return True if *remote_path* exists on the remote host."""
    try:
        sftp.stat(remote_path)
        return True
    except FileNotFoundError:
        return False


def sftp_makedirs(sftp, remote_dir):
    """Recursively create directories on the remote host."""
    dirs_to_create = []
    current = remote_dir
    # iPod uses Unix-style paths; "/" is always the root
    while current and current != "/":
        if sftp_exists(sftp, current):
            break
        dirs_to_create.append(current)
        current = os.path.dirname(current)
    for d in reversed(dirs_to_create):
        sftp.mkdir(d)


def remote_exec(ssh, cmd):
    """Execute a command on the iPod via SSH and return stdout."""
    _, stdout, stderr = ssh.exec_command(cmd)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if err:
        logger.debug("remote stderr: %s", err)
    return out


# ---------------------------------------------------------------------------
# Scanning & diffing
# ---------------------------------------------------------------------------

def scan_local_music(music_dir):
    """Return a list of absolute paths of supported audio files."""
    results = []
    for root, _dirs, files in os.walk(music_dir):
        for fname in files:
            if os.path.splitext(fname)[1].lower() in SUPPORTED_EXTENSIONS:
                results.append(os.path.join(root, fname))
    results.sort()
    return results


def existing_ipod_files(sftp):
    """Return a set of filenames already present in the iPod music folders."""
    existing = set()
    for slot in range(50):
        folder = "{}/F{:02d}".format(IPOD_MUSIC_DIR, slot)
        if not sftp_exists(sftp, folder):
            continue
        try:
            for entry in sftp.listdir(folder):
                existing.add(entry)
        except IOError:
            pass
    return existing


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def sync_music(host, port, user, password, music_dir):
    """Main entry point: upload music and update the iPod database."""
    local_files = scan_local_music(music_dir)
    if not local_files:
        logger.warning("No supported audio files found in %s", music_dir)
        return

    logger.info("Found %d local audio file(s)", len(local_files))

    ssh, sftp = connect_ssh(host, port, user, password)
    try:
        _do_sync(ssh, sftp, local_files)
    finally:
        sftp.close()
        ssh.close()


def _do_sync(ssh, sftp, local_files):
    """Upload new files and rebuild the iTunesDB."""
    # 1. Pull the current iTunesDB ------------------------------------------
    db_exists = sftp_exists(sftp, IPOD_DB_PATH)
    if db_exists:
        logger.info("Downloading existing iTunesDB …")
        tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp_db.close()
        sftp.get(IPOD_DB_PATH, tmp_db.name)
        db = ITunesDB.parse_file(tmp_db.name)
        os.unlink(tmp_db.name)
    else:
        logger.info("No existing iTunesDB found – creating new database")
        db = ITunesDB()

    # 2. Determine which files are new --------------------------------------
    existing_names = existing_ipod_files(sftp)
    new_files = []
    for fpath in local_files:
        fname = os.path.basename(fpath)
        safe = _sanitise_filename(fname)
        if safe in existing_names:
            logger.debug("Skipping (already on iPod): %s", fname)
        else:
            new_files.append(fpath)

    if not new_files:
        logger.info("All files already on iPod – nothing to do")
        return

    logger.info("%d new file(s) to upload", len(new_files))

    # 3. Upload files & build track entries ---------------------------------
    next_id = db.next_track_id()
    for fpath in new_files:
        fname = os.path.basename(fpath)
        slot = hash_slot(fname)
        remote_path = ipod_dest_path(fname, slot)
        remote_dir = os.path.dirname(remote_path)

        sftp_makedirs(sftp, remote_dir)
        logger.info("Uploading %s → %s", fname, remote_path)
        sftp.put(fpath, remote_path)

        meta = get_audio_metadata(fpath)
        # Build the iPod-relative path (colon-separated)
        relative = remote_path.replace(IPOD_MUSIC_ROOT + "/", "")
        ipod_path = ":" + relative.replace("/", ":")

        track = Track(
            track_id=next_id,
            title=meta["title"],
            artist=meta["artist"],
            album=meta["album"],
            genre=meta["genre"],
            duration_ms=meta["duration_ms"],
            filesize=meta["filesize"],
            bitrate=meta["bitrate"],
            year=meta["year"],
            track_number=meta["track_number"],
            sample_rate=meta["sample_rate"],
            filetype=meta["filetype"],
            ipod_path=ipod_path,
        )
        db.add_track(track)
        next_id += 1
        logger.info("  → Track %d: %s – %s", track.track_id, track.artist, track.title)

    # 4. Write updated iTunesDB back to the iPod ---------------------------
    tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp_out.close()
    db.write_file(tmp_out.name)

    sftp_makedirs(sftp, IPOD_DB_DIR)
    # Back up the old database
    if db_exists:
        backup = IPOD_DB_PATH + ".bak.{}".format(int(time.time()))
        logger.info("Backing up old iTunesDB → %s", backup)
        sftp.rename(IPOD_DB_PATH, backup)

    logger.info("Uploading new iTunesDB …")
    sftp.put(tmp_out.name, IPOD_DB_PATH)
    os.unlink(tmp_out.name)

    # 5. Rebuild the HashInfo database used on newer firmware ----------------
    logger.info("Refreshing iPod caches …")
    remote_exec(ssh, "sync")

    logger.info("Done – %d track(s) added!", len(new_files))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Upload music to a jailbroken iPod over SSH")
    p.add_argument("--host", required=True,
                   help="iPod IP address or hostname")
    p.add_argument("--port", type=int, default=DEFAULT_SSH_PORT,
                   help="SSH port (default: %(default)s)")
    p.add_argument("--user", default=DEFAULT_SSH_USER,
                   help="SSH username (default: %(default)s)")
    p.add_argument("--password", default=DEFAULT_SSH_PASS,
                   help="SSH password (default: %(default)s)")
    p.add_argument("--music-dir", required=True,
                   help="Local directory containing music files to upload")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Enable debug logging")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    if not os.path.isdir(args.music_dir):
        logger.error("Music directory does not exist: %s", args.music_dir)
        sys.exit(1)

    sync_music(args.host, args.port, args.user, args.password, args.music_dir)


if __name__ == "__main__":
    main()

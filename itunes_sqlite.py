import random
import string
import time
import os
import hashlib

# Mac epoch: seconds between 1904-01-01 and 1970-01-01
_MAC_EPOCH = 2082844800

def _mac_timestamp():
    """Current time as a Mac-epoch timestamp."""
    return int(time.time()) + _MAC_EPOCH

def generate_hashed_filename(filename):
    """Generate a deterministic 4-character filename based on input filename hash."""
    base, ext = os.path.splitext(filename)
    # Use SHA256 of the filename to get a deterministic hash
    h = hashlib.sha256(filename.encode('utf-8')).hexdigest().upper()
    name = h[:4]
    return name + ext

class IOSDatabase:
    """Helper to generate SQL for iOS MediaLibrary.sqlitedb."""

    def __init__(self):
        self.tracks = []
        self.artists = {}  # name -> pid
        self.albums = {}   # (title, artist) -> pid
        self.base_location_id = 1

    def add_track(self, meta):
        """Add a track to the database.

        meta: dict with keys matching ipod_sync.py's get_audio_metadata + ipod_path
        """
        # Generate a unique PID for the track
        # Use random 64-bit integer (signed)
        meta['pid'] = random.randint(-9223372036854775808, 9223372036854775807)
        self.tracks.append(meta)

    def _get_or_create_artist(self, name):
        if not name:
            return 0
        if name in self.artists:
            return self.artists[name]
        pid = random.randint(-9223372036854775808, 9223372036854775807)
        self.artists[name] = pid
        return pid

    def _get_or_create_album(self, title, artist):
        if not title:
            return 0
        key = (title, artist)
        if key in self.albums:
            return self.albums[key]
        pid = random.randint(-9223372036854775808, 9223372036854775807)
        self.albums[key] = pid
        return pid

    def generate_sql(self):
        """Generate SQL statements to insert all tracks."""
        sql = []
        sql.append("BEGIN TRANSACTION;")

        # Ensure base_location exists
        # We use INSERT OR IGNORE to avoid errors if it exists
        sql.append("INSERT OR IGNORE INTO base_location (id, path) VALUES (1, 'iTunes_Control/Music');")

        for track in self.tracks:
            # 1. Artist
            artist_pid = self._get_or_create_artist(track.get('artist'))
            if artist_pid:
                # INSERT OR IGNORE INTO artist
                sql.append(f"INSERT OR IGNORE INTO artist (pid, name) VALUES ({artist_pid}, '{self._escape(track.get('artist'))}');")

            # 2. Album
            album_pid = self._get_or_create_album(track.get('album'), track.get('artist'))
            if album_pid:
                # INSERT OR IGNORE INTO album
                sql.append(f"INSERT OR IGNORE INTO album (pid, name, artist_pid) VALUES ({album_pid}, '{self._escape(track.get('album'))}', {artist_pid});")

            # 3. Item
            # Map filetype to media kind / kind_id
            # 1 = Song
            media_kind = 1

            # Escape strings
            title = self._escape(track.get('title', ''))
            artist = self._escape(track.get('artist', ''))
            album = self._escape(track.get('album', ''))
            genre = self._escape(track.get('genre', ''))

            # Timestamp
            ts = _mac_timestamp()

            # File info
            filesize = track.get('filesize', 0)
            duration = track.get('duration_ms', 0)
            year = track.get('year', 0)
            track_num = track.get('track_number', 0)
            bitrate = track.get('bitrate', 128)

            # Insert into item
            # We omit many fields for brevity, hoping defaults/NULLs work.
            # Essential fields based on libgpod:
            # pid, media_kind, date_modified, title, artist, album, genre, year, track_number,
            # album_pid, artist_pid, total_time_ms

            sql.append(f"""
INSERT INTO item (
    pid, media_kind, date_modified,
    title, artist, album, genre,
    year, track_number,
    album_pid, artist_pid,
    total_time_ms, exclude_from_shuffle, remember_bookmark
) VALUES (
    {track['pid']}, {media_kind}, {ts},
    '{title}', '{artist}', '{album}', '{genre}',
    {year}, {track_num},
    {album_pid}, {artist_pid},
    {duration}, 0, 0
);
""")

            # 4. Location
            # Convert :iTunes_Control:Music:F00:ABCD.mp3 to F00/ABCD.mp3
            # We assume the path starts with :iTunes_Control:Music: or similar
            ipod_path = track.get('ipod_path', '')
            if ipod_path.startswith(':'):
                rel_path = ipod_path[1:].replace(':', '/')
            else:
                rel_path = ipod_path.replace(':', '/')

            # Strip base path if present
            base_prefix = "iTunes_Control/Music/"
            if rel_path.startswith(base_prefix):
                rel_path = rel_path[len(base_prefix):]

            # location_type 0x46494C45 ('FILE') = 1179208773
            location_type = 1179208773

            # extension marker (libgpod uses filetype_marker e.g. 'MP3 ' = 0x4d503320)
            # Simplified: just use 0 or try to match if critical.
            # Assuming 0 is safe or maybe just omit? No, insert needs it.
            # Let's try to map common exts
            ext = track.get('filetype', '').lower()
            if ext == '.mp3':
                kind_id = 1
                fmt = 0x4d503320 # 'MP3 '
            elif ext in ('.m4a', '.aac'):
                kind_id = 1
                fmt = 0x4d344120 # 'M4A '
            else:
                kind_id = 1
                fmt = 0

            sql.append(f"""
INSERT INTO location (
    item_pid, sub_id, base_location_id,
    location_type, location, extension, kind_id,
    date_created, file_size
) VALUES (
    {track['pid']}, 0, {self.base_location_id},
    {location_type}, '{rel_path}', {fmt}, {kind_id},
    {ts}, {filesize}
);
""")

        sql.append("COMMIT;")
        return "\n".join(sql)

    def _escape(self, s):
        """Escape single quotes for SQL."""
        if not s:
            return ""
        return str(s).replace("'", "''")

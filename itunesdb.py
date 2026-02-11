"""
iTunesDB binary format parser and writer for old iPods.

The iTunesDB is a binary file used by iPods to store track metadata.  Its
layout consists of nested records identified by 4-byte magic strings:

    mhbd  – Database header (top-level container)
    ├─ mhsd (type 1) – Track list section
    │  └─ mhlt
    │     └─ mhit  – One per track
    │        └─ mhod – String/data objects (title, artist, path, …)
    ├─ mhsd (type 2) – Playlist section
    │  └─ mhlp
    │     └─ mhyp  – Playlist header
    │        └─ mhip – Playlist item
    └─ mhsd (type 3) – Podcast section (often absent)

This module supports:
  • Parsing an existing iTunesDB into Python objects
  • Creating a brand-new iTunesDB from scratch
  • Adding tracks (with all required mhod sub-records)
  • Serialising everything back to the binary format
"""

import struct
import time

# ---------------------------------------------------------------------------
# mhod type constants
# ---------------------------------------------------------------------------
MHOD_TITLE = 1
MHOD_LOCATION = 2     # colon-separated iPod path
MHOD_ALBUM = 3
MHOD_ARTIST = 4
MHOD_GENRE = 5
MHOD_FILETYPE = 6
MHOD_COMMENT = 7
MHOD_COMPOSER = 12
MHOD_SORT_TITLE = 27
MHOD_SORT_ALBUM = 28
MHOD_SORT_ARTIST = 29

# We only write the string-type mhods we care about.
STRING_MHOD_TYPES = {
    MHOD_TITLE, MHOD_LOCATION, MHOD_ALBUM, MHOD_ARTIST,
    MHOD_GENRE, MHOD_FILETYPE,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_MAC_EPOCH = 2082844800  # seconds between 1904-01-01 and 1970-01-01


def _mac_timestamp():
    """Current time as a Mac-epoch timestamp (seconds since 1904-01-01)."""
    return int(time.time()) + _MAC_EPOCH


def _encode_utf16(text):
    """Encode a Python string to UTF-16-LE bytes (no BOM)."""
    return text.encode("utf-16-le")


def _decode_utf16(data):
    """Decode UTF-16-LE bytes to a Python string."""
    return data.decode("utf-16-le", errors="replace").rstrip("\x00")


# ---------------------------------------------------------------------------
# Track data class
# ---------------------------------------------------------------------------

class Track:
    """Represents a single track (song) on the iPod."""

    def __init__(self, track_id=0, title="", artist="", album="",
                 genre="", duration_ms=0, filesize=0, bitrate=128,
                 year=0, track_number=0, sample_rate=44100,
                 filetype=".mp3", ipod_path=""):
        self.track_id = track_id
        self.title = title
        self.artist = artist
        self.album = album
        self.genre = genre
        self.duration_ms = duration_ms
        self.filesize = filesize
        self.bitrate = bitrate
        self.year = year
        self.track_number = track_number
        self.sample_rate = sample_rate
        self.filetype = filetype
        self.ipod_path = ipod_path   # colon-separated path e.g. :iTunes_Control:Music:F00:song.mp3

    def __repr__(self):
        return "<Track {}: {} – {}>".format(self.track_id, self.artist, self.title)


# ---------------------------------------------------------------------------
# mhod (object data) builder / parser
# ---------------------------------------------------------------------------

def _build_string_mhod(mhod_type, text):
    """Build a binary string-type mhod record.

    Layout:
        0x00  4s   magic       "mhod"
        0x04  I    header_len  24
        0x08  I    total_len   header_len + body_len
        0x0C  I    mhod_type
        0x10  I    unk1        0
        0x14  I    unk2        0
        -- body (string sub-header + UTF-16 payload) --
        0x18  I    unk3        1
        0x1C  I    str_len     length of UTF-16 data
        0x20  I    unk4        0
        0x24  I    unk5        0
        0x28  ...  UTF-16-LE string data
    """
    encoded = _encode_utf16(text)
    str_len = len(encoded)
    body_header = struct.pack("<4I", 1, str_len, 0, 0)   # 16 bytes
    header_len = 24
    total_len = header_len + len(body_header) + str_len
    header = struct.pack("<4s5I",
                         b"mhod", header_len, total_len,
                         mhod_type, 0, 0)
    return header + body_header + encoded


def _parse_string_mhod_body(data, offset, total_len, header_len):
    """Parse the body of a string-type mhod and return the decoded string."""
    body_offset = offset + header_len
    if body_offset + 16 > offset + total_len:
        return ""
    _unk3, str_len, _unk4, _unk5 = struct.unpack_from("<4I", data, body_offset)
    str_start = body_offset + 16
    str_end = str_start + str_len
    if str_end > offset + total_len:
        str_end = offset + total_len
    return _decode_utf16(data[str_start:str_end])


# ---------------------------------------------------------------------------
# mhit builder / parser
# ---------------------------------------------------------------------------

_MHIT_HEADER_LEN = 156  # we use a fixed 156-byte mhit header


def _build_mhit(track, mhod_bytes_list):
    """Build a binary mhit record for *track* with its mhod children."""
    mhod_count = len(mhod_bytes_list)
    mhod_total = sum(len(b) for b in mhod_bytes_list)
    total_len = _MHIT_HEADER_LEN + mhod_total

    ts = _mac_timestamp()

    filetype_code = 0x000D  # MP3 = 0x000D, AAC/M4A = 0x0002, WAV = 0x0004, AIFF = 0x0001
    ext = track.filetype.lower()
    if ext in (".m4a", ".aac", ".mp4"):
        filetype_code = 0x0002
    elif ext in (".wav",):
        filetype_code = 0x0004
    elif ext in (".aiff", ".aif"):
        filetype_code = 0x0001

    # Build the 156-byte mhit header
    # We zero-fill fields we don't need and set the ones that matter.
    buf = bytearray(b"\x00" * _MHIT_HEADER_LEN)
    struct.pack_into("<4s", buf, 0, b"mhit")
    struct.pack_into("<I", buf, 4, _MHIT_HEADER_LEN)     # header_len
    struct.pack_into("<I", buf, 8, total_len)             # total_len
    struct.pack_into("<I", buf, 12, mhod_count)           # mhod_count
    struct.pack_into("<I", buf, 16, track.track_id)       # unique_id
    struct.pack_into("<I", buf, 20, 1)                    # visible
    struct.pack_into("<I", buf, 24, filetype_code)        # filetype_code (4s encoded)
    struct.pack_into("<H", buf, 28, 0)                    # type (0 = audio)
    struct.pack_into("<H", buf, 30, 0)                    # compilation
    struct.pack_into("<I", buf, 32, 5)                    # rating (0-100)
    struct.pack_into("<I", buf, 36, ts)                   # last_modified
    struct.pack_into("<I", buf, 40, track.filesize)       # filesize
    struct.pack_into("<I", buf, 44, track.duration_ms)    # duration_ms
    struct.pack_into("<I", buf, 48, track.track_number)   # track_number
    struct.pack_into("<I", buf, 52, 0)                    # total_tracks
    struct.pack_into("<I", buf, 56, track.year)           # year
    struct.pack_into("<I", buf, 60, track.bitrate)        # bitrate
    struct.pack_into("<I", buf, 64, track.sample_rate * 65536)  # sample_rate (fixed-point 16.16)
    struct.pack_into("<I", buf, 68, 0)                    # volume
    struct.pack_into("<I", buf, 72, 0)                    # start_time
    struct.pack_into("<I", buf, 76, 0)                    # stop_time
    struct.pack_into("<I", buf, 80, 0)                    # soundcheck
    struct.pack_into("<I", buf, 84, 0)                    # play_count
    struct.pack_into("<I", buf, 88, 0)                    # play_count2
    struct.pack_into("<I", buf, 92, 0)                    # last_played
    struct.pack_into("<I", buf, 96, 0)                    # disc_number
    struct.pack_into("<I", buf, 100, 0)                   # total_discs
    struct.pack_into("<I", buf, 104, 0)                   # user_id
    struct.pack_into("<I", buf, 108, ts)                  # date_added
    struct.pack_into("<I", buf, 112, 0)                   # bookmark_time
    struct.pack_into("<I", buf, 116, track.track_id)      # dbid_lo
    struct.pack_into("<I", buf, 120, 0)                   # dbid_hi
    struct.pack_into("<I", buf, 124, 0)                   # checked
    struct.pack_into("<I", buf, 128, 0)                   # app_rating
    struct.pack_into("<H", buf, 132, 0)                   # bpm
    struct.pack_into("<H", buf, 134, 0)                   # artwork_count
    struct.pack_into("<I", buf, 136, 0)                   # unk9
    struct.pack_into("<I", buf, 140, 0)                   # artwork_size
    struct.pack_into("<I", buf, 144, 0)                   # unk11
    struct.pack_into("<f", buf, 148, 0.0)                 # sample_rate_float
    struct.pack_into("<I", buf, 152, 0)                   # release_date

    return bytes(buf) + b"".join(mhod_bytes_list)


# ---------------------------------------------------------------------------
# iTunes DB class
# ---------------------------------------------------------------------------

class ITunesDB:
    """In-memory representation of an iTunesDB."""

    DB_VERSION = 0x19  # version 25 – widely compatible

    def __init__(self):
        self.tracks = []
        self._raw = None          # original raw bytes (if parsed)
        self._max_track_id = 0

    # ----- public API -------------------------------------------------------

    def add_track(self, track):
        if track.track_id > self._max_track_id:
            self._max_track_id = track.track_id
        self.tracks.append(track)

    def next_track_id(self):
        return self._max_track_id + 1

    # ----- parsing ----------------------------------------------------------

    @classmethod
    def parse_file(cls, path):
        """Parse an iTunesDB binary file and return an ITunesDB instance."""
        with open(path, "rb") as fh:
            data = fh.read()
        return cls.parse_bytes(data)

    @classmethod
    def parse_bytes(cls, data):
        """Parse raw iTunesDB bytes and return an ITunesDB instance."""
        db = cls()
        db._raw = data
        if len(data) < 104:
            return db   # empty / corrupt

        magic = data[0:4]
        if magic != b"mhbd":
            raise ValueError("Not a valid iTunesDB (bad magic: {!r})".format(magic))

        header_len = struct.unpack_from("<I", data, 4)[0]
        total_len = struct.unpack_from("<I", data, 8)[0]
        num_mhsd = struct.unpack_from("<I", data, 12)[0]

        offset = header_len
        for _ in range(num_mhsd):
            if offset + 12 > len(data):
                break
            sec_magic = data[offset:offset + 4]
            if sec_magic != b"mhsd":
                break
            sec_header_len = struct.unpack_from("<I", data, offset + 4)[0]
            sec_total_len = struct.unpack_from("<I", data, offset + 8)[0]
            sec_type = struct.unpack_from("<I", data, offset + 12)[0]

            if sec_type == 1:
                db._parse_track_list(data, offset + sec_header_len,
                                     offset + sec_total_len)
            # We skip playlists / podcasts for now
            offset += sec_total_len

        return db

    def _parse_track_list(self, data, start, end):
        """Parse the mhlt section containing tracks."""
        if start + 12 > end:
            return
        magic = data[start:start + 4]
        if magic != b"mhlt":
            return
        header_len = struct.unpack_from("<I", data, start + 4)[0]
        num_tracks = struct.unpack_from("<I", data, start + 8)[0]

        offset = start + header_len
        for _ in range(num_tracks):
            if offset + 12 > end:
                break
            trk_magic = data[offset:offset + 4]
            if trk_magic != b"mhit":
                break
            trk_header_len = struct.unpack_from("<I", data, offset + 4)[0]
            trk_total_len = struct.unpack_from("<I", data, offset + 8)[0]
            mhod_count = struct.unpack_from("<I", data, offset + 12)[0]
            track_id = struct.unpack_from("<I", data, offset + 16)[0]

            # Read basic numeric fields from mhit header
            duration_ms = 0
            filesize = 0
            bitrate = 0
            year = 0
            track_number = 0
            sample_rate = 44100

            if trk_header_len >= 48:
                filesize = struct.unpack_from("<I", data, offset + 40)[0]
                duration_ms = struct.unpack_from("<I", data, offset + 44)[0]
            if trk_header_len >= 52:
                track_number = struct.unpack_from("<I", data, offset + 48)[0]
            if trk_header_len >= 60:
                year = struct.unpack_from("<I", data, offset + 56)[0]
            if trk_header_len >= 64:
                bitrate = struct.unpack_from("<I", data, offset + 60)[0]
            if trk_header_len >= 68:
                sr_raw = struct.unpack_from("<I", data, offset + 64)[0]
                sample_rate = sr_raw >> 16 if sr_raw > 65535 else sr_raw

            # Parse associated mhods
            mhod_offset = offset + trk_header_len
            strings = {}
            for _ in range(mhod_count):
                if mhod_offset + 24 > offset + trk_total_len:
                    break
                m_magic = data[mhod_offset:mhod_offset + 4]
                if m_magic != b"mhod":
                    break
                m_header_len = struct.unpack_from("<I", data, mhod_offset + 4)[0]
                m_total_len = struct.unpack_from("<I", data, mhod_offset + 8)[0]
                m_type = struct.unpack_from("<I", data, mhod_offset + 12)[0]

                if m_type in STRING_MHOD_TYPES:
                    text = _parse_string_mhod_body(data, mhod_offset,
                                                   m_total_len, m_header_len)
                    strings[m_type] = text
                mhod_offset += m_total_len

            track = Track(
                track_id=track_id,
                title=strings.get(MHOD_TITLE, ""),
                artist=strings.get(MHOD_ARTIST, ""),
                album=strings.get(MHOD_ALBUM, ""),
                genre=strings.get(MHOD_GENRE, ""),
                ipod_path=strings.get(MHOD_LOCATION, ""),
                filetype=strings.get(MHOD_FILETYPE, ".mp3"),
                duration_ms=duration_ms,
                filesize=filesize,
                bitrate=bitrate,
                year=year,
                track_number=track_number,
                sample_rate=sample_rate,
            )
            self.tracks.append(track)
            if track_id > self._max_track_id:
                self._max_track_id = track_id

            offset += trk_total_len

    # ----- writing ----------------------------------------------------------

    def write_file(self, path):
        """Serialise the database to a binary file."""
        data = self.to_bytes()
        with open(path, "wb") as fh:
            fh.write(data)

    def to_bytes(self):
        """Serialise the whole database to bytes."""
        # 1. Build track section (mhsd type 1)
        track_section = self._build_track_section()
        # 2. Build master-playlist section (mhsd type 2)
        playlist_section = self._build_playlist_section()
        # 3. Wrap in mhbd
        return self._build_mhbd(track_section, playlist_section)

    def _build_track_section(self):
        """Build the mhsd(type=1) → mhlt → mhit* blob."""
        mhit_blobs = []
        for track in self.tracks:
            mhods = self._build_track_mhods(track)
            mhit_blobs.append(_build_mhit(track, mhods))

        # mhlt header (12 bytes)
        mhlt_header_len = 12
        mhlt_body = b"".join(mhit_blobs)
        mhlt = struct.pack("<4s2I", b"mhlt", mhlt_header_len, len(self.tracks))
        mhlt += mhlt_body

        # mhsd header (type 1)
        mhsd_header_len = 96
        mhsd_total_len = mhsd_header_len + len(mhlt)
        mhsd_buf = bytearray(b"\x00" * mhsd_header_len)
        struct.pack_into("<4s", mhsd_buf, 0, b"mhsd")
        struct.pack_into("<I", mhsd_buf, 4, mhsd_header_len)
        struct.pack_into("<I", mhsd_buf, 8, mhsd_total_len)
        struct.pack_into("<I", mhsd_buf, 12, 1)  # type = tracks
        return bytes(mhsd_buf) + mhlt

    def _build_playlist_section(self):
        """Build a minimal mhsd(type=2) with a master playlist containing all tracks."""
        # mhip entries (one per track)
        mhip_blobs = []
        for track in self.tracks:
            mhip = self._build_mhip(track.track_id)
            mhip_blobs.append(mhip)

        # mhyp (playlist header)
        mhyp_header_len = 108
        mhyp_body = b"".join(mhip_blobs)
        mhyp_buf = bytearray(b"\x00" * mhyp_header_len)
        struct.pack_into("<4s", mhyp_buf, 0, b"mhyp")
        struct.pack_into("<I", mhyp_buf, 4, mhyp_header_len)
        struct.pack_into("<I", mhyp_buf, 8, mhyp_header_len + len(mhyp_body))
        struct.pack_into("<I", mhyp_buf, 12, 0)                    # mhod count in playlist = 0
        struct.pack_into("<I", mhyp_buf, 16, len(self.tracks))     # item count
        struct.pack_into("<I", mhyp_buf, 20, 1)                    # is_master = 1
        struct.pack_into("<I", mhyp_buf, 24, _mac_timestamp())     # timestamp
        struct.pack_into("<I", mhyp_buf, 28, 0)                    # playlist_id
        struct.pack_into("<I", mhyp_buf, 44, 1)                    # sort_order
        mhyp = bytes(mhyp_buf) + mhyp_body

        # mhlp header
        mhlp_header_len = 12
        mhlp = struct.pack("<4s2I", b"mhlp", mhlp_header_len, 1)  # 1 playlist
        mhlp += mhyp

        # mhsd (type 2)
        mhsd_header_len = 96
        mhsd_total_len = mhsd_header_len + len(mhlp)
        mhsd_buf = bytearray(b"\x00" * mhsd_header_len)
        struct.pack_into("<4s", mhsd_buf, 0, b"mhsd")
        struct.pack_into("<I", mhsd_buf, 4, mhsd_header_len)
        struct.pack_into("<I", mhsd_buf, 8, mhsd_total_len)
        struct.pack_into("<I", mhsd_buf, 12, 2)  # type = playlists
        return bytes(mhsd_buf) + mhlp

    @staticmethod
    def _build_mhip(track_id):
        """Build a playlist item record."""
        header_len = 76
        buf = bytearray(b"\x00" * header_len)
        struct.pack_into("<4s", buf, 0, b"mhip")
        struct.pack_into("<I", buf, 4, header_len)
        struct.pack_into("<I", buf, 8, header_len)  # total_len (no children)
        struct.pack_into("<I", buf, 12, 0)          # mhod_count
        struct.pack_into("<I", buf, 16, 0)          # podcast_group_flag
        struct.pack_into("<I", buf, 20, 0)          # group_id
        struct.pack_into("<I", buf, 24, track_id)   # track_id
        return bytes(buf)

    def _build_mhbd(self, track_section, playlist_section):
        """Wrap sections in the top-level mhbd header."""
        mhbd_header_len = 104
        children = track_section + playlist_section
        total_len = mhbd_header_len + len(children)

        buf = bytearray(b"\x00" * mhbd_header_len)
        struct.pack_into("<4s", buf, 0, b"mhbd")
        struct.pack_into("<I", buf, 4, mhbd_header_len)
        struct.pack_into("<I", buf, 8, total_len)
        struct.pack_into("<I", buf, 12, 2)                     # num_mhsd children
        struct.pack_into("<I", buf, 16, self.DB_VERSION)       # db version
        struct.pack_into("<I", buf, 20, len(self.tracks))      # num_tracks (informational)
        struct.pack_into("<Q", buf, 24, 0)                     # db_id
        struct.pack_into("<I", buf, 36, 2)                     # unk / platform (2 = iPod)
        struct.pack_into("<I", buf, 40, 0)                     # unk
        return bytes(buf) + children

    @staticmethod
    def _build_track_mhods(track):
        """Build the list of mhod records for a track."""
        mhods = []
        if track.title:
            mhods.append(_build_string_mhod(MHOD_TITLE, track.title))
        if track.ipod_path:
            mhods.append(_build_string_mhod(MHOD_LOCATION, track.ipod_path))
        if track.album:
            mhods.append(_build_string_mhod(MHOD_ALBUM, track.album))
        if track.artist:
            mhods.append(_build_string_mhod(MHOD_ARTIST, track.artist))
        if track.genre:
            mhods.append(_build_string_mhod(MHOD_GENRE, track.genre))
        if track.filetype:
            mhods.append(_build_string_mhod(MHOD_FILETYPE, track.filetype))
        return mhods

"""Tests for ipod_sync and itunesdb modules."""

import os
import struct
import tempfile
import unittest
from unittest import mock

from itunesdb import (
    ITunesDB,
    Track,
    MHOD_TITLE,
    MHOD_LOCATION,
    MHOD_ALBUM,
    MHOD_ARTIST,
    MHOD_GENRE,
    _build_string_mhod,
    _parse_string_mhod_body,
    _encode_utf16,
    _decode_utf16,
)
from ipod_sync import (
    get_audio_metadata,
    hash_slot,
    ipod_dest_path,
    scan_local_music,
    _sanitise_filename,
    parse_args,
    SUPPORTED_EXTENSIONS,
)


# ---------------------------------------------------------------------------
# itunesdb tests
# ---------------------------------------------------------------------------

class TestUTF16Helpers(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        text = "Hello World"
        self.assertEqual(_decode_utf16(_encode_utf16(text)), text)

    def test_encode_decode_unicode(self):
        text = "Ünïcödé Tëst"
        self.assertEqual(_decode_utf16(_encode_utf16(text)), text)

    def test_empty_string(self):
        self.assertEqual(_decode_utf16(_encode_utf16("")), "")


class TestMhodBuild(unittest.TestCase):
    def test_string_mhod_magic(self):
        data = _build_string_mhod(MHOD_TITLE, "Test Song")
        self.assertEqual(data[:4], b"mhod")

    def test_string_mhod_type(self):
        data = _build_string_mhod(MHOD_ARTIST, "Artist")
        mhod_type = struct.unpack_from("<I", data, 12)[0]
        self.assertEqual(mhod_type, MHOD_ARTIST)

    def test_string_mhod_roundtrip(self):
        text = "My Great Song"
        data = _build_string_mhod(MHOD_TITLE, text)
        header_len = struct.unpack_from("<I", data, 4)[0]
        total_len = struct.unpack_from("<I", data, 8)[0]
        parsed = _parse_string_mhod_body(data, 0, total_len, header_len)
        self.assertEqual(parsed, text)


class TestTrack(unittest.TestCase):
    def test_defaults(self):
        t = Track()
        self.assertEqual(t.track_id, 0)
        self.assertEqual(t.title, "")
        self.assertEqual(t.artist, "")
        self.assertEqual(t.album, "")

    def test_repr(self):
        t = Track(track_id=42, artist="Beatles", title="Help")
        self.assertIn("42", repr(t))
        self.assertIn("Beatles", repr(t))
        self.assertIn("Help", repr(t))


class TestITunesDB(unittest.TestCase):
    def test_empty_db(self):
        db = ITunesDB()
        self.assertEqual(len(db.tracks), 0)
        self.assertEqual(db.next_track_id(), 1)

    def test_add_track(self):
        db = ITunesDB()
        t = Track(track_id=1, title="Song", artist="Art", album="Alb",
                  ipod_path=":iTunes_Control:Music:F00:song.mp3")
        db.add_track(t)
        self.assertEqual(len(db.tracks), 1)
        self.assertEqual(db.next_track_id(), 2)

    def test_serialise_and_parse_roundtrip(self):
        """Create a DB with tracks, serialise, parse back, verify."""
        db = ITunesDB()
        db.add_track(Track(
            track_id=1, title="First Song", artist="Artist A",
            album="Album X", genre="Rock", duration_ms=240000,
            filesize=5000000, bitrate=320, year=2020,
            track_number=1, sample_rate=44100, filetype=".mp3",
            ipod_path=":iTunes_Control:Music:F00:first.mp3",
        ))
        db.add_track(Track(
            track_id=2, title="Second Song", artist="Artist B",
            album="Album Y", genre="Pop", duration_ms=180000,
            filesize=3000000, bitrate=256, year=2021,
            track_number=2, sample_rate=48000, filetype=".m4a",
            ipod_path=":iTunes_Control:Music:F01:second.m4a",
        ))

        data = db.to_bytes()

        # Verify top-level magic
        self.assertEqual(data[:4], b"mhbd")

        # Parse back
        db2 = ITunesDB.parse_bytes(data)
        self.assertEqual(len(db2.tracks), 2)

        t1 = db2.tracks[0]
        self.assertEqual(t1.track_id, 1)
        self.assertEqual(t1.title, "First Song")
        self.assertEqual(t1.artist, "Artist A")
        self.assertEqual(t1.album, "Album X")
        self.assertEqual(t1.genre, "Rock")
        self.assertEqual(t1.ipod_path, ":iTunes_Control:Music:F00:first.mp3")
        self.assertEqual(t1.duration_ms, 240000)
        self.assertEqual(t1.filesize, 5000000)
        self.assertEqual(t1.bitrate, 320)
        self.assertEqual(t1.year, 2020)
        self.assertEqual(t1.track_number, 1)

        t2 = db2.tracks[1]
        self.assertEqual(t2.track_id, 2)
        self.assertEqual(t2.title, "Second Song")
        self.assertEqual(t2.artist, "Artist B")

    def test_write_and_read_file(self):
        db = ITunesDB()
        db.add_track(Track(
            track_id=10, title="File Test", artist="File Artist",
            ipod_path=":iTunes_Control:Music:F05:file.mp3",
        ))
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db.write_file(path)
            db2 = ITunesDB.parse_file(path)
            self.assertEqual(len(db2.tracks), 1)
            self.assertEqual(db2.tracks[0].title, "File Test")
        finally:
            os.unlink(path)

    def test_parse_invalid_magic_raises(self):
        with self.assertRaises(ValueError):
            ITunesDB.parse_bytes(b"BADx" + b"\x00" * 200)

    def test_parse_empty_data(self):
        db = ITunesDB.parse_bytes(b"")
        self.assertEqual(len(db.tracks), 0)


# ---------------------------------------------------------------------------
# ipod_sync tests
# ---------------------------------------------------------------------------

class TestSanitiseFilename(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(_sanitise_filename("song.mp3"), "song.mp3")

    def test_spaces_replaced(self):
        result = _sanitise_filename("my song (1).mp3")
        self.assertNotIn(" ", result)
        self.assertNotIn("(", result)
        self.assertNotIn(")", result)


class TestHashSlot(unittest.TestCase):
    def test_range(self):
        for name in ["a.mp3", "b.m4a", "xyz.wav"]:
            slot = hash_slot(name)
            self.assertGreaterEqual(slot, 0)
            self.assertLessEqual(slot, 49)

    def test_deterministic(self):
        self.assertEqual(hash_slot("test.mp3"), hash_slot("test.mp3"))


class TestIpodDestPath(unittest.TestCase):
    def test_path_format(self):
        path = ipod_dest_path("song.mp3", 5)
        self.assertIn("/F05/", path)
        self.assertTrue(path.startswith("/private/var/mobile/Media"))
        self.assertTrue(path.endswith("song.mp3"))


class TestScanLocalMusic(unittest.TestCase):
    def test_finds_supported_files(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ["a.mp3", "b.m4a", "c.txt", "d.wav", "e.py"]:
                open(os.path.join(d, name), "w").close()
            results = scan_local_music(d)
            basenames = {os.path.basename(r) for r in results}
            self.assertIn("a.mp3", basenames)
            self.assertIn("b.m4a", basenames)
            self.assertIn("d.wav", basenames)
            self.assertNotIn("c.txt", basenames)
            self.assertNotIn("e.py", basenames)

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(scan_local_music(d), [])


class TestParseArgs(unittest.TestCase):
    def test_required_args(self):
        args = parse_args(["--host", "192.168.1.10", "--music-dir", "/tmp/music"])
        self.assertEqual(args.host, "192.168.1.10")
        self.assertEqual(args.music_dir, "/tmp/music")
        self.assertEqual(args.port, 22)
        self.assertEqual(args.user, "root")
        self.assertEqual(args.password, "alpine")

    def test_custom_port(self):
        args = parse_args(["--host", "10.0.0.1", "--port", "2222",
                           "--music-dir", "/music"])
        self.assertEqual(args.port, 2222)


class TestGetAudioMetadata(unittest.TestCase):
    def test_invalid_audio_data_returns_defaults(self):
        """Metadata for a file with invalid audio data should return safe defaults."""
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"\x00" * 1024)
            path = f.name
        try:
            meta = get_audio_metadata(path)
            self.assertIn("title", meta)
            self.assertIn("artist", meta)
            self.assertEqual(meta["filesize"], 1024)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()

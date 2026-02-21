import unittest
import string
import random
import os
import sys

# Add the parent directory to sys.path to import ipod_sync and itunes_sqlite
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from itunes_sqlite import IOSDatabase, generate_hashed_filename
except ImportError:
    # Allow tests to be defined even if module doesn't exist yet (for TDD)
    IOSDatabase = None
    generate_hashed_filename = None

class TestIOSFeatures(unittest.TestCase):
    def test_generate_hashed_filename(self):
        if generate_hashed_filename is None:
            self.skipTest("itunes_sqlite module not found")

        # Test deterministic hashing
        filename = "test_song.mp3"
        hashed = generate_hashed_filename(filename)
        self.assertTrue(hashed.endswith(".mp3"))
        base = os.path.splitext(hashed)[0]
        self.assertEqual(len(base), 4)
        self.assertTrue(all(c in string.ascii_uppercase + string.digits for c in base))

        # Verify deterministic
        hashed2 = generate_hashed_filename(filename)
        self.assertEqual(hashed, hashed2)

    def test_ios_database_sql_generation(self):
        if IOSDatabase is None:
            self.skipTest("itunes_sqlite module not found")

        db = IOSDatabase()
        meta = {
            "title": "Test Title",
            "artist": "Test Artist",
            "album": "Test Album",
            "genre": "Test Genre",
            "duration_ms": 180000,
            "filesize": 1024000,
            "bitrate": 128,
            "year": 2023,
            "track_number": 1,
            "sample_rate": 44100,
            "filetype": ".mp3",
            "ipod_path": ":iTunes_Control:Music:F00:ABCD.mp3"
        }
        db.add_track(meta)
        sql = db.generate_sql()

        # Verify essential parts of the SQL
        self.assertIn("INSERT INTO item", sql)
        self.assertIn("Test Title", sql)
        self.assertIn("Test Artist", sql)
        self.assertIn("Test Album", sql)
        # Should be converted to relative path with slashes
        self.assertIn("F00/ABCD.mp3", sql)
        self.assertIn("INSERT OR IGNORE INTO base_location", sql)
        self.assertIn("INSERT INTO location", sql)

if __name__ == '__main__':
    unittest.main()

"""Offline tests for the Android MyBible registry parser."""
import io
import json
import unittest
import zipfile
from wordstudy.module_downloader import parse_registry, DownloadError

class RegistryTests(unittest.TestCase):
    def sample(self):
        return {
            "hosts": [{"alias": "mirror", "path": "https://example.org/%s"}],
            "downloads": [
                {"abr": "KJV", "fil": "KJV.SQLite3", "lng": "en", "des": "King James Version",
                 "url": ["{mirror}KJV.SQLite3"], "upd": "2026-01-01", "siz": "4 MB"},
                {"abr": "ABC", "fil": "ABC.commentaries.SQLite3.zip", "lng": "en",
                 "url": ["{mirror}ABC.commentaries.SQLite3.zip"]},
                {"abr": "DICT", "fil": "DICT.dictionary.SQLite3", "lng": "en",
                 "url": ["https://example.org/DICT.dictionary.SQLite3"]},
                {"abr": "BAD", "fil": "../BAD.SQLite3", "url": ["{mirror}BAD.SQLite3"]},
                {"abr": "UNSUPPORTED", "fil": "UNSUPPORTED.book.SQLite3", "url": ["{mirror}x"]},
            ],
        }

    def test_json_catalog(self):
        result = parse_registry(json.dumps(self.sample()).encode())
        self.assertEqual({m.abbreviation for m in result}, {"KJV", "ABC", "DICT"})
        self.assertEqual({m.kind for m in result}, {"Bible", "Commentary", "Dictionary"})
        self.assertEqual(next(m for m in result if m.abbreviation == "KJV").urls,
                         ("https://example.org/KJV.SQLite3",))

    def test_zipped_catalog(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("registry.json", json.dumps(self.sample()))
        self.assertEqual(len(parse_registry(out.getvalue())), 3)

    def test_invalid_catalog(self):
        with self.assertRaises(DownloadError):
            parse_registry(b'{"downloads": {}}')

if __name__ == "__main__":
    unittest.main()

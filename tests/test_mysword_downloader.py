"""Offline tests for the MySword website catalog."""
import unittest
from unittest.mock import patch
from wordstudy.mysword_downloader import parse_mysword_page, fetch_mysword_catalog

class MySwordCatalogTests(unittest.TestCase):
    def test_public_download_link(self):
        html = b'''<html><h3>Language: English (2)</h3><table>
        <tr><td><a href="https://mysword-bible.info/download/getfile.php?file=acv.bbl.mybible.gz">A Conservative Version</a></td></tr>
        <tr><td><a href="/download/getfile.php?file=tsk.cmt.mybible.gz">Treasury of Scripture Knowledge</a></td></tr>
        </table></html>'''
        entries = parse_mysword_page(html, "Bible")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].language, "English")
        self.assertEqual(entries[0].filename, "acv.bbl.mybible.gz")
        self.assertEqual(entries[0].name, "A Conservative Version")

    def test_selected_category_only(self):
        html = b'<h3>Language: English (1)</h3><a href="/getfile.php?file=test.bbl.mybible.gz">Test</a>'
        with patch("wordstudy.mysword_downloader._read_url", return_value=html) as request:
            entries, errors = fetch_mysword_catalog("bibles", "English")
        self.assertEqual(errors, [])
        self.assertEqual(len(entries), 1)
        request.assert_called_once()
        self.assertTrue(request.call_args.args[0].endswith("/bibles"))

    def test_language_filter(self):
        html = (b'<h3>Language: English (1)</h3>'
                b'<a href="/getfile.php?file=en.bbl.mybible.gz">English</a>'
                b'<h3>Language: Spanish (1)</h3>'
                b'<a href="/getfile.php?file=es.bbl.mybible.gz">Spanish</a>')
        with patch("wordstudy.mysword_downloader._read_url", return_value=html):
            entries, _ = fetch_mysword_catalog("bibles", "Spanish")
        self.assertEqual([e.name for e in entries], ["Spanish"])

    def test_invalid_category_never_fetches(self):
        with patch("wordstudy.mysword_downloader._read_url") as request:
            with self.assertRaises(ValueError):
                fetch_mysword_catalog("all", "English")
            request.assert_not_called()

    def test_exclude_unrelated_links(self):
        html = b'<a href="https://example.org/other.zip">Other</a>'
        self.assertEqual(parse_mysword_page(html, "Bible"), [])

    def test_duplicate_links(self):
        html = b'''<a href="https://mysword-bible.info/download/getfile.php?file=x.bbl.mybible.gz">X</a>
        <a href="https://mysword-bible.info/download/getfile.php?file=x.bbl.mybible.gz">X</a>'''
        self.assertEqual(len(parse_mysword_page(html, "Bible")), 1)

if __name__ == "__main__":
    unittest.main()

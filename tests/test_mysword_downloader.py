"""Offline tests for the MySword website catalog."""
import unittest
from wordstudy.mysword_downloader import parse_mysword_page

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

    def test_exclude_unrelated_links(self):
        html = b'<a href="https://example.org/other.zip">Other</a>'
        self.assertEqual(parse_mysword_page(html, "Bible"), [])

    def test_duplicate_links(self):
        html = b'''<a href="https://mysword-bible.info/download/getfile.php?file=x.bbl.mybible.gz">X</a>
        <a href="https://mysword-bible.info/download/getfile.php?file=x.bbl.mybible.gz">X</a>'''
        self.assertEqual(len(parse_mysword_page(html, "Bible")), 1)

if __name__ == "__main__":
    unittest.main()

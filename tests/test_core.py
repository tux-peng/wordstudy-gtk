"""Run with python3 -m unittest discover -s tests -v; needs no GTK/display."""
import gzip
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from wordstudy.markup import plain_text, styled_spans
from wordstudy.modules import Library, ModuleError, inspect_module
from wordstudy.references import MYBIBLE_BOOK_IDS, MYBIBLE_TO_BOOK, Reference, parse_reference
from wordstudy.preferences import DisplayPreferences, PreferencesStore, compact_module_name
from wordstudy.sample import create_samples
from wordstudy.storage import UserData


class ReferenceTests(unittest.TestCase):
    def test_reference_text_and_numeric(self):
        self.assertEqual(parse_reference("John 3:16"), Reference(43, 3, 16))
        self.assertEqual(parse_reference("Jn 3:16"), Reference(43, 3, 16))
        self.assertEqual(parse_reference("43.3.16"), Reference(43, 3, 16))
        self.assertEqual(parse_reference("1 Cor 13:4"), Reference(46, 13, 4))
        self.assertEqual(parse_reference("Revelation 22:21"), Reference(66, 22, 21))
        with self.assertRaises(ValueError):
            parse_reference("Unknownbook 1:1")

    def test_gbf_rendering_and_safe_links(self):
        spans = styled_spans("<FR>loved<Fr> <WG25> <RX43.3.16> <FI>added<Fi>", bible=True)
        text = "".join(s.text for s in spans)
        self.assertIn("loved", text)
        self.assertIn("G25", text)
        self.assertIn("added", text)
        self.assertIn("sG25", [s.link for s in spans])
        self.assertIn("b43.3.16", [s.link for s in spans])
        self.assertIn("red", spans[0].styles)
        self.assertEqual(plain_text("<p>a</p><script>exploit()</script><p>b</p>").strip(), "a\nb")
        self.assertEqual([s.link for s in styled_spans('<a href="javascript:evil()">unsafe</a>')], [None])

    def test_mysword_display_filters_and_red_letters(self):
        verse = '<FR>Jesus says<Fr> love<WG25><WTN-NSF> <WH2617>'
        self.assertIn('red', styled_spans(verse, bible=True)[0].styles)
        hidden = styled_spans(verse, bible=True, show_strongs=False,
                              show_morphology=False, words_of_christ_red=False)
        self.assertEqual(''.join(s.text for s in hidden).strip(), 'Jesus says love')
        self.assertNotIn('red', [style for s in hidden for style in s.styles])
        self.assertNotIn('sG25', [s.link for s in hidden])
        self.assertIn('mN-NSF', [s.link for s in styled_spans(verse, bible=True)])
        self.assertIn('red', styled_spans('<FR>Peace</FR>', bible=True)[0].styles)
        self.assertIn('red', styled_spans('<font color="red">Peace</font>', bible=True)[0].styles)
        self.assertIn('red', styled_spans('<span style="color:#FF0000">Peace</span>', bible=True)[0].styles)
        self.assertNotIn('red', [style for s in styled_spans(
            '<font color="red">Peace</font>', bible=True, words_of_christ_red=False)
            for style in s.styles])
        self.assertEqual(styled_spans("<img src='2.jpg'/>")[0].image, '2.jpg')
        self.assertFalse(any(s.image for s in styled_spans('<img src="https://example.com/x.jpg"/>')))
        links = styled_spans("<a class='dict' href='#j Places Recorded'>Places</a>")
        self.assertIn('j-Places Recorded', [s.link for s in links if s.link])

    def test_mybible_red_letters_strongs_and_morphology(self):
        verse = '<J>Love <i>everyone</i></J> <S>25</S><m>V-AAI-3S</m>'
        spans = styled_spans(verse, bible=True, format='mybible', book=43)
        self.assertIn('red', spans[0].styles)
        self.assertIn('sG25', [s.link for s in spans])
        self.assertIn('mV-AAI-3S', [s.link for s in spans])
        hidden = styled_spans(verse, bible=True, format='mybible', book=43,
                              show_strongs=False, show_morphology=False,
                              words_of_christ_red=False)
        self.assertEqual(''.join(s.text for s in hidden).strip(), 'Love everyone')
        self.assertNotIn('red', [style for s in hidden for style in s.styles])
        self.assertIn('sH7225', [s.link for s in styled_spans(
            '<S>7225</S>', bible=True, format='mybible', book=1)])
        links = styled_spans('<a href="B:500 3:16">John</a> '
                             '<a href="S:G25">love</a> '
                             '<a href="S:grace">grace</a>')
        self.assertEqual([s.link for s in links if s.link],
                         ['b43.3.16', 'sG25', 'dgrace'])
        lower = styled_spans('<j>Peace be with you</j>', bible=True, format='mybible', book=43)
        self.assertIn('red', lower[0].styles)
        # KJ3-style tags must still paint red if format is left at the default.
        self.assertIn('red', styled_spans('<J>For God so loved</J>', bible=True)[0].styles)


class ModuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.samples = create_samples(self.root / "samples")
        self.library = Library(self.root / "user")

    def test_all_types(self):
        types = {inspect_module(p).kind for p in self.samples}
        self.assertEqual(types, {"bbl", "cmt", "dct", "bok", "jor"})
        for path in self.samples:
            self.library.import_file(path)
        self.assertEqual(len(self.library.installed), 5)
        self.assertEqual(self.library.by_kind("bbl")[0].abbreviation, "DemoBible")

    def test_bible_navigation_search(self):
        bible = self.library.import_file(self.samples[0])[0]
        self.assertEqual(self.library.books(bible), [1, 19, 43, 45])
        self.assertEqual(self.library.chapters(bible, 43), [3])
        john = self.library.verses(bible, 43, 3)
        self.assertEqual([v.ref.verse for v in john], [16, 17])
        self.assertIn("loved", plain_text(john[0].raw, bible=True))
        results = self.library.search_bible(bible, "loved")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].ref, Reference(43, 3, 16))
        # Literal % and _ should not turn into SQL wildcard searches.
        self.assertEqual(len(self.library.search_bible(bible, "%")), 0)

    def test_commentary_dictionary_books_and_library_search(self):
        modules = {m.kind: m for p in self.samples for m in self.library.import_file(p)}
        refs = self.library.commentaries(modules["cmt"], Reference(43, 3, 17))
        self.assertEqual(len(refs), 1)
        self.assertIn("Study question", refs[0].raw)
        self.assertEqual(self.library.dictionary(modules["dct"], "G25")[0].title, "G25")
        self.assertIn("How to study a passage", self.library.topics(modules["bok"]))
        self.assertIn("Observation", self.library.article(modules["bok"], "How to study a passage"))
        self.assertIn("Study prompts", self.library.topics(modules["jor"]))
        self.assertTrue(self.library.search_library("observation"))

    def test_compressed_imports_and_bad_file(self):
        contents = self.samples[0].read_bytes()
        gz = self.root / "test.bbl.mybible.gz"
        gz.write_bytes(gzip.compress(contents))
        self.assertEqual(self.library.import_file(gz)[0].kind, "bbl")
        zip_path = self.root / "study.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("../../test.dct.mybible", self.samples[2].read_bytes())
        imported = self.library.import_file(zip_path)
        self.assertEqual(imported[0].kind, "dct")
        self.assertTrue(str(imported[0].path).startswith(str(self.library.module_dir)))
        invalid = self.root / "bad.bbl.mybible"
        invalid.write_bytes(b"not a database")
        with self.assertRaises(ModuleError):
            self.library.import_file(invalid)

    def test_storage(self):
        data = UserData(self.root / "user")
        ref = Reference(43, 3, 16)
        self.assertEqual(data.get_note(ref), "")
        data.save_note(ref, "Study the context")
        self.assertEqual(data.get_note(ref), "Study the context")
        self.assertTrue(data.toggle_bookmark(ref))
        self.assertEqual(data.bookmarks(), [ref])
        self.assertFalse(data.toggle_bookmark(ref))
        data.save_note(ref, "")
        self.assertEqual(data.get_note(ref), "")

    def _mybible_fixture(self, name: str, kind: str):
        path = self.root / name
        conn = sqlite3.connect(path)
        conn.execute('CREATE TABLE info(name TEXT, value TEXT)')
        conn.executemany('INSERT INTO info VALUES (?,?)', [
            ('description', 'Synthetic MyBible module'), ('language', 'en'),
            ('right_to_left', 'false')])
        if kind == 'bbl':
            conn.execute('CREATE TABLE books(book_number INT, book_color TEXT, short_name TEXT, long_name TEXT)')
            conn.executemany('INSERT INTO books VALUES (?, ?, ?, ?)', [
                (10, '#ccc', 'Gen', 'Genesis'), (500, '#ccc', 'John', 'John')])
            conn.execute('CREATE TABLE verses(book_number INT, chapter INT, verse INT, text TEXT)')
            conn.executemany('INSERT INTO verses VALUES (?,?,?,?)', [
                (10,1,1,'In the beginning <S>7225</S>'),
                (500,3,16,'<J>For God so loved the world</J> <S>25</S><m>A-NOM</m>')])
        elif kind == 'cmt':
            conn.execute('CREATE TABLE commentaries('
                         'book_number INT, chapter_number_from INT, verse_number_from INT, '
                         'chapter_number_to INT, verse_number_to INT, marker TEXT, text TEXT)')
            conn.executemany('INSERT INTO commentaries VALUES (?,?,?,?,?,?,?)', [
                (500,0,0,None,None,'','Book introduction'),
                (500,3,0,None,None,'','Chapter overview'),
                (500,3,16,None,None,'','Verse 16 commentary'),
                (500,3,10,3,17,'','Extended commentary'),
                (500,3,18,None,None,'','Not relevant to verse 16'),
                (500,3,22,4,10,'','Spanning two chapters')])
        elif kind == 'dct':
            conn.execute('CREATE TABLE dictionary(topic TEXT, definition TEXT)')
            conn.executemany('INSERT INTO dictionary VALUES (?, ?)', [
                ('G25', 'to love'), ('grace', 'undeserved favor')])
        conn.commit()
        conn.close()
        return path

    def test_mybible_bible_real_schema_import_and_search(self):
        file = self._mybible_fixture('Example.SQLite3', 'bbl')
        module = self.library.import_file(file)[0]
        self.assertEqual(module.format, 'mybible')
        self.assertEqual(module.abbreviation, 'Example')
        self.assertEqual(self.library.books(module), [1, 43])
        self.assertEqual(self.library.chapters(module, 43), [3])
        john = self.library.verses(module, 43, 3)
        self.assertEqual(john[0].ref, Reference(43, 3, 16))
        self.assertIn('loved', plain_text(john[0].raw, bible=True, format='mybible', book=43))
        self.assertEqual(self.library.search_bible(module, 'loved')[0].ref, Reference(43, 3, 16))
        self.assertEqual(len(self.library.search_bible(module, '%')), 0)
        self.assertEqual(self.library.refresh()[0].format, 'mybible')
        self.assertEqual(len(MYBIBLE_BOOK_IDS), 66)
        self.assertEqual(MYBIBLE_TO_BOOK[500], 43)

    def test_kj3_marks_john_316_as_words_of_christ(self):
        path = Path(__file__).resolve().parent.parent / "KJ3.SQLite3"
        if not path.is_file():
            self.skipTest("KJ3.SQLite3 is not in the project tree")
        module = inspect_module(path)
        self.assertEqual(module.format, "mybible")
        john = self.library.verses(module, 43, 3)
        verse = next(item for item in john if item.ref.verse == 16)
        self.assertIn("<J>", verse.raw)
        spans = styled_spans(verse.raw, bible=True, format=module.format, book=43)
        self.assertTrue(any("red" in span.styles for span in spans))
        self.assertIn("loved", "".join(span.text for span in spans))

    def test_afv_mysword_renders_without_inventing_red_letters(self):
        path = Path(__file__).resolve().parent.parent / "AFV+.bbl.mybible"
        if not path.is_file():
            self.skipTest("AFV+.bbl.mybible is not in the project tree")
        module = inspect_module(path)
        self.assertEqual(module.format, "mysword")
        verse = next(item for item in self.library.verses(module, 43, 3) if item.ref.verse == 16)
        spans = styled_spans(verse.raw, bible=True, format=module.format, book=43)
        self.assertIn("loved", "".join(span.text for span in spans).lower())
        self.assertNotIn("<FR>", verse.raw)
        self.assertFalse(any("red" in span.styles for span in spans))

    def test_map_collection_is_a_book_with_images(self):
        path = Path(__file__).resolve().parent.parent / "DavidRumseyBibleMapsCollection.bok.jor.mybible"
        if not path.is_file():
            self.skipTest("map collection is not in the project tree")
        module = inspect_module(path)
        self.assertEqual(module.kind, "bok")
        topics = self.library.topics(module)
        self.assertTrue(any("Moses" in topic for topic in topics))
        topic = next(item for item in topics if "Moses" in item)
        html = self.library.article(module, topic) or ""
        self.assertTrue(any(span.image == "2.jpg" for span in styled_spans(html)))
        blob = self.library.resource(module, "2.jpg")
        self.assertTrue(blob and blob[:2] == b"\xff\xd8")
        self.assertIsNone(self.library.resource(module, "../2.jpg"))

    def test_mybible_commentary_dictionary_and_archive(self):
        comment = self.library.import_file(self._mybible_fixture(
            'Example.commentaries.SQLite3', 'cmt'))[0]
        dictionary = self.library.import_file(self._mybible_fixture(
            'Example.dictionary.SQLite3', 'dct'))[0]
        refs = self.library.commentaries(comment, Reference(43, 3, 16))
        self.assertEqual(len(refs), 4)
        self.assertIn('Extended commentary', [hit.raw for hit in refs])
        self.assertEqual(self.library.commentaries(comment, Reference(43, 4, 2))[-1].raw,
                         'Spanning two chapters')
        self.assertEqual(self.library.dictionary(dictionary, 'G25')[0].raw, 'to love')
        self.assertTrue(self.library.search_library('overview'))
        with zipfile.ZipFile(self.root / 'Modules.zip', 'w') as archive:
            archive.write(dictionary.path, 'from-archive.dictionary.SQLite3')
        self.assertEqual(self.library.import_file(self.root / 'Modules.zip')[0].kind, 'dct')
        gzip_path = self.root / 'Example.SQLite3.gz'
        gzip_path.write_bytes(gzip.compress(self._mybible_fixture('Example.SQLite3', 'bbl').read_bytes()))
        self.assertEqual(self.library.import_file(gzip_path)[0].format, 'mybible')

    def test_mybible_unsupported_module_is_rejected(self):
        path = self.root / 'x.crossreferences.SQLite3'
        conn = sqlite3.connect(path)
        conn.execute('CREATE TABLE info(name TEXT, value TEXT)')
        conn.execute('CREATE TABLE cross_references(book INT)')
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(ModuleError, 'Unsupported MyBible module type'):
            self.library.import_file(path)


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = PreferencesStore(self.root)

    def test_default_and_roundtrip_font_and_wrap_prefs(self):
        defaults = self.store.load()
        self.assertTrue(defaults.wrap_titles)
        self.assertTrue(defaults.wrap_bible)
        self.assertEqual(defaults.bible_font, "Noto Serif 15")
        self.assertTrue(defaults.show_strongs)
        self.assertTrue(defaults.show_morphology)
        self.assertTrue(defaults.words_of_christ_red)
        custom = DisplayPreferences(
            bible_font="Liberation Serif 18", study_font="DejaVu Sans 12",
            notes_font="Monospace 11", title_font="Sans Bold 20",
            wrap_bible=False, wrap_study=True, wrap_notes=False,
            wrap_titles=False, show_strongs=False,
            show_morphology=False, words_of_christ_red=False,
        )
        self.store.save(custom)
        self.assertEqual(self.store.load(), custom)
        self.assertTrue(self.store.path.is_file())

    def test_invalid_or_extra_prefs_keep_safe_defaults(self):
        self.store.path.write_text("not json", encoding="utf-8")
        self.assertEqual(self.store.load(), DisplayPreferences())
        self.store.path.write_text(json.dumps({
            "bible_font": "", "study_font": "Unreasonably " * 50,
            "notes_font": "Liberation Mono 12", "wrap_bible": "no",
            "wrap_titles": False, "irrelevant": "ignored",
        }), encoding="utf-8")
        prefs = self.store.load()
        self.assertEqual(prefs.bible_font, "Noto Serif 15")
        self.assertEqual(prefs.study_font, "Noto Serif 15")
        self.assertEqual(prefs.notes_font, "Liberation Mono 12")
        self.assertTrue(prefs.wrap_bible)
        self.assertFalse(prefs.wrap_titles)

    def test_long_module_title_does_not_escape_selector_limit(self):
        long = "Very Detailed Exposition of the Epistle " * 300
        display = compact_module_name("EXPOSITIONS", long)
        self.assertEqual(len(display), 38)
        self.assertTrue(display.endswith("…"))
        self.assertEqual(compact_module_name("KJV", "King James Version"),
                         "KJV — King James Version")
        self.assertEqual(compact_module_name("", "A Short Title"), "A Short Title")
        self.assertLessEqual(len(compact_module_name("X" * 200, "")), 38)
        with self.assertRaises(ValueError):
            compact_module_name("ABC", "Name", 1)


if __name__ == "__main__":
    unittest.main()

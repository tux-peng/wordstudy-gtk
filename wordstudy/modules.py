"""MySword SQLite module discovery and read-only access.

The program never modifies installed MySword files. Schema introspection is
intentional: content modules in circulation have some column-name variants.
"""
from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .markup import plain_text
from .references import MYBIBLE_BOOK_IDS, MYBIBLE_TO_BOOK, Reference

SQLITE_MAGIC = b"SQLite format 3\x00"
MAX_UNPACKED_SIZE = 350 * 1024 * 1024
SUPPORTED = ("bbl", "cmt", "dct", "bok", "jor")
TABLE_NAMES = {"bbl": "Bible", "cmt": "Commentary", "dct": "Dictionary", "bok": "Book", "jor": "Journal"}
EXTENSIONS = (".mybible", ".sqlite3", ".mybible.gz", ".sqlite3.gz", ".zip", ".gz")


class ModuleError(Exception):
    """Invalid, unsupported, or locked content module."""


@dataclass(frozen=True)
class Module:
    path: Path
    kind: str
    title: str
    abbreviation: str
    language: str = ""
    rtl: bool = False
    format: str = "mysword"

    @property
    def label(self) -> str:
        if self.abbreviation and self.title != self.abbreviation:
            return f"{self.abbreviation} — {self.title}"
        return self.title


@dataclass(frozen=True)
class Verse:
    ref: Reference
    raw: str


@dataclass(frozen=True)
class Hit:
    module: Module
    title: str
    raw: str
    ref: Reference | None = None


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    # URI percent-escaping is performed by as_uri(). mode=ro disallows writes.
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        yield conn
    finally:
        conn.close()


def _tables(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0].lower(): r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    )}


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {r[1].lower(): r[1] for r in conn.execute(f"PRAGMA table_info({_q(table)})")}


def _col(columns: dict[str, str], *options: str) -> str | None:
    for option in options:
        if option.lower() in columns:
            return columns[option.lower()]
    return None


def _require(columns: dict[str, str], *options: str) -> str:
    value = _col(columns, *options)
    if value is None:
        raise ModuleError(f"Missing required column (one of {', '.join(options)})")
    return _q(value)


def _text_column(kind: str, columns: dict[str, str]) -> str:
    options = {
        "bbl": ("Scripture", "Text", "Content"),
        "cmt": ("Scripture", "Comments", "Commentary", "Text", "Content", "Data"),
        "dct": ("Definition", "Meaning", "Text", "Content", "Data"),
        "bok": ("Scripture", "Content", "Data", "Text", "Article", "Body"),
        "jor": ("Scripture", "Content", "Data", "Text", "Article", "Body"),
    }
    return _require(columns, *options[kind])


def _resolve_schema(conn: sqlite3.Connection, kind: str) -> tuple[str, dict[str, str]]:
    tables = _tables(conn)
    table = tables.get(TABLE_NAMES[kind].lower())
    if table is None and kind in ("bok", "jor"):
        table = tables.get("book") or tables.get("journal")
    if table is None:
        raise ModuleError(f"No {TABLE_NAMES[kind]} table")
    cols = _columns(conn, table)
    _text_column(kind, cols)
    if kind in ("bbl", "cmt"):
        _require(cols, "Book")
        _require(cols, "Chapter")
        if kind == "bbl":
            _require(cols, "Verse")
    if kind == "dct":
        _require(cols, "Word", "Topic", "Name", "Key")
    if kind in ("bok", "jor"):
        _require(cols, "Topic", "Title", "Subject", "Name")
    return table, cols


def inspect_module(path: str | Path) -> Module:
    """Validate an uncompressed module. Reject unsupported or non-SQLite files."""
    path = Path(path)
    if not path.is_file():
        raise ModuleError(f"File not found: {path}")
    is_my_bible = path.name.lower().endswith(".sqlite3")
    if not is_my_bible and not path.name.lower().endswith(".mybible"):
        raise ModuleError("Expected a .mybible or .SQLite3 SQLite module")
    if path.stat().st_size < 100 or path.stat().st_size > MAX_UNPACKED_SIZE:
        raise ModuleError("Module file is empty or exceeds the size limit")
    with path.open("rb") as f:
        if f.read(16) != SQLITE_MAGIC:
            raise ModuleError("Not an unencrypted SQLite MySword module")
    try:
        with _connect(path) as conn:
            tables = _tables(conn)
            if is_my_bible:
                if "info" not in tables:
                    raise ModuleError("MyBible module needs an info(name, value) table")
                info_cols = _columns(conn, tables["info"])
                _require(info_cols, "name")
                _require(info_cols, "value")
                metadata = {str(r[0]).lower(): r[1] for r in conn.execute(
                    f"SELECT {_require(info_cols, 'name')}, {_require(info_cols, 'value')} FROM {_q(tables['info'])}"
                ) if r[0] is not None}
                if "verses" in tables:
                    kind, table, required = "bbl", "verses", ("book_number", "chapter", "verse", "text")
                elif "commentaries" in tables:
                    kind, table, required = "cmt", "commentaries", ("book_number", "chapter_number_from", "verse_number_from", "text")
                elif "dictionary" in tables:
                    kind, table, required = "dct", "dictionary", ("topic", "definition")
                else:
                    raise ModuleError("Unsupported MyBible module type. Supported: Bible, commentary, dictionary")
                columns = _columns(conn, tables[table])
                for column in required:
                    _require(columns, column)
                if kind == "bbl" and not ("books" in tables or "books_all" in tables):
                    raise ModuleError("MyBible Bible needs a books or books_all table")
                suffixes = (".commentaries.sqlite3", ".dictionary.sqlite3", ".sqlite3")
                stem = path.name
                for suffix in suffixes:
                    if stem.lower().endswith(suffix):
                        stem = stem[:-len(suffix)]
                        break
                return Module(
                    path.resolve(), kind, str(metadata.get("description") or stem).strip(),
                    stem, str(metadata.get("language") or ""),
                    str(metadata.get("right_to_left") or "false").lower() in ("1", "true"), "mybible",
                )
            inferred = next((kind for kind, table in TABLE_NAMES.items() if table.lower() in tables), None)
            suffix = path.name.lower().removesuffix(".mybible").rsplit(".", 1)[-1]
            kind = suffix if suffix in SUPPORTED else inferred
            # Map collections (.bok.jor.mybible with a map table) belong with books.
            if "map" in tables and ("journal" in tables or "book" in tables):
                kind = "bok"
            if kind is None:
                raise ModuleError("Unsupported module type; expected Bible, Commentary, Dictionary, Book or Journal")
            _resolve_schema(conn, kind)
            detail = None
            if "details" in tables:
                detail = conn.execute(f"SELECT * FROM {_q(tables['details'])} LIMIT 1").fetchone()
            metadata = {key.lower(): detail[key] for key in detail.keys()} if detail else {}
            fallback = path.name.split(".")[0]
            title = str(metadata.get("description") or metadata.get("title") or fallback)
            abbreviation = str(metadata.get("abbreviation") or fallback)
            lang = str(metadata.get("language") or "")
            rtl = str(metadata.get("righttoleft") or "0") in ("1", "True", "true")
            return Module(path.resolve(), kind, title, abbreviation, lang, rtl)
    except sqlite3.DatabaseError as error:
        raise ModuleError(f"Unreadable SQLite module: {error}") from error


class Library:
    def __init__(self, data_dir: str | Path | None = None):
        root = data_dir or Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "wordstudy-gtk"
        self.data_dir = Path(root)
        self.module_dir = self.data_dir / "modules"
        self.module_dir.mkdir(parents=True, exist_ok=True)
        self.errors: list[str] = []
        self.installed: list[Module] = []
        self.refresh()

    def refresh(self) -> list[Module]:
        self.installed = []
        self.errors = []
        for path in sorted(p for p in self.module_dir.rglob("*") if p.name.lower().endswith((".mybible", ".sqlite3"))):
            if not path.is_file():
                continue
            try:
                self.installed.append(inspect_module(path))
            except (ModuleError, OSError) as exc:
                self.errors.append(f"{path.name}: {exc}")
        self.installed.sort(key=lambda m: (SUPPORTED.index(m.kind), m.abbreviation.casefold()))
        return self.installed

    def by_kind(self, kind: str) -> list[Module]:
        return [m for m in self.installed if m.kind == kind]

    def import_file(self, filename: str | Path) -> list[Module]:
        """Import MySword/MyBible SQLite files, gzip, or zip, validating each."""
        path = Path(filename)
        if not path.is_file():
            raise ModuleError(f"File not found: {path}")
        candidates: list[tuple[str, bytes | Path]] = []
        low = path.name.lower()
        if low.endswith((".mybible", ".sqlite3")):
            candidates.append((path.name, path))
        elif low.endswith(".gz"):
            name = path.name[:-3]
            if not name.lower().endswith((".mybible", ".sqlite3")):
                raise ModuleError("GZip needs to contain a .mybible or .SQLite3 module")
            with gzip.open(path, "rb") as z:
                content = z.read(MAX_UNPACKED_SIZE + 1)
            if len(content) > MAX_UNPACKED_SIZE:
                raise ModuleError("Uncompressed file is too large")
            candidates.append((name, content))
        elif low.endswith(".zip"):
            try:
                with zipfile.ZipFile(path) as z:
                    for entry in z.infolist():
                        if entry.is_dir() or not entry.filename.lower().endswith((".mybible", ".sqlite3")):
                            continue
                        if entry.file_size > MAX_UNPACKED_SIZE:
                            raise ModuleError("Zip member is too large")
                        if sum(len(c) if isinstance(c, bytes) else c.stat().st_size for _, c in candidates) + entry.file_size > MAX_UNPACKED_SIZE:
                            raise ModuleError("Archive is too large")
                        # Basename only. Never follow paths from inside an archive.
                        content = z.read(entry)
                        candidates.append((Path(entry.filename).name, content))
            except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
                raise ModuleError(f"Bad archive: {exc}") from exc
            if not candidates:
                raise ModuleError("Zip archive contains no .mybible or .SQLite3 modules")
        else:
            raise ModuleError("Select .mybible, .SQLite3, .gz, or .zip")
        imported: list[Module] = []
        for basename, source in candidates:
            # Validate a temporary copy BEFORE putting it into the library.
            suffix = ".sqlite3" if basename.lower().endswith(".sqlite3") else ".mybible"
            fd, temp = tempfile.mkstemp(suffix=suffix, dir=self.module_dir)
            os.close(fd)
            staging = Path(temp)
            try:
                if isinstance(source, Path):
                    if source.stat().st_size > MAX_UNPACKED_SIZE:
                        raise ModuleError("Module too large")
                    shutil.copyfile(source, staging)
                else:
                    staging.write_bytes(source)
                metadata = inspect_module(staging)
                # Restore the useful original filename and type extension.
                if metadata.format == "mysword" and not (
                    basename.lower().endswith(f".{metadata.kind}.mybible")
                    or f".{metadata.kind}." in basename.lower()
                ):
                    basename = Path(basename).name.removesuffix(".mybible") + f".{metadata.kind}.mybible"
                target_dir = self.module_dir / metadata.kind
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / Path(basename).name
                n = 2
                while target.exists():
                    stem = basename[:-len(suffix)]
                    target = target_dir / f"{stem}-{n}{suffix}"
                    n += 1
                staging.replace(target)
                imported.append(inspect_module(target))
            except (OSError, sqlite3.DatabaseError) as exc:
                raise ModuleError(f"Import failed: {exc}") from exc
            finally:
                staging.unlink(missing_ok=True)
        self.refresh()
        return imported

    def books(self, bible: Module) -> list[int]:
        if bible.format == "mybible":
            with _connect(bible.path) as conn:
                return [MYBIBLE_TO_BOOK[int(r[0])] for r in conn.execute(
                    'SELECT DISTINCT book_number FROM verses ORDER BY book_number'
                ) if r[0] is not None and int(r[0]) in MYBIBLE_TO_BOOK]
        with _connect(bible.path) as conn:
            table, cols = _resolve_schema(conn, "bbl")
            return [int(r[0]) for r in conn.execute(
                f"SELECT DISTINCT {_require(cols, 'Book')} FROM {_q(table)} ORDER BY 1"
            ) if r[0] is not None and 1 <= int(r[0]) <= 66]

    def chapters(self, bible: Module, book: int) -> list[int]:
        if bible.format == "mybible":
            if not 1 <= book <= 66:
                return []
            with _connect(bible.path) as conn:
                return [int(r[0]) for r in conn.execute(
                    'SELECT DISTINCT chapter FROM verses WHERE book_number=? ORDER BY chapter',
                    (MYBIBLE_BOOK_IDS[book - 1],)
                ) if r[0] is not None and int(r[0]) > 0]
        with _connect(bible.path) as conn:
            table, cols = _resolve_schema(conn, "bbl")
            return [int(r[0]) for r in conn.execute(
                f"SELECT DISTINCT {_require(cols, 'Chapter')} FROM {_q(table)} "
                f"WHERE {_require(cols, 'Book')}=? ORDER BY 1", (book,)
            ) if r[0] is not None and int(r[0]) > 0]

    def verses(self, bible: Module, book: int, chapter: int) -> list[Verse]:
        if bible.format == "mybible":
            if not 1 <= book <= 66:
                return []
            with _connect(bible.path) as conn:
                rows = conn.execute(
                    'SELECT verse, text FROM verses WHERE book_number=? AND chapter=? ORDER BY verse',
                    (MYBIBLE_BOOK_IDS[book - 1], chapter),
                ).fetchall()
                return [Verse(Reference(book, chapter, int(r[0])), str(r[1] or ""))
                        for r in rows if r[0] is not None and int(r[0]) > 0]
        with _connect(bible.path) as conn:
            table, cols = _resolve_schema(conn, "bbl")
            book_col = _require(cols, "Book")
            chapter_col = _require(cols, "Chapter")
            verse_col = _require(cols, "Verse")
            rows = conn.execute(
                f"SELECT {verse_col}, {_text_column('bbl', cols)} FROM {_q(table)} "
                f"WHERE {book_col}=? AND {chapter_col}=? ORDER BY {verse_col}",
                (book, chapter),
            ).fetchall()
            return [Verse(Reference(book, chapter, int(r[0])), str(r[1] or "")) for r in rows if r[0] is not None and int(r[0]) > 0]

    def commentaries(self, commentary: Module, ref: Reference, limit: int = 25) -> list[Hit]:
        if commentary.format == "mybible":
            with _connect(commentary.path) as conn:
                rows = conn.execute(
                    'SELECT chapter_number_from, verse_number_from, '
                    'chapter_number_to, verse_number_to, text FROM commentaries '
                    'WHERE book_number=? AND chapter_number_from<=? '
                    'AND (chapter_number_to IS NULL OR chapter_number_to=0 OR chapter_number_to>=?) '
                    'ORDER BY chapter_number_from, verse_number_from',
                    (MYBIBLE_BOOK_IDS[ref.book - 1], ref.chapter, ref.chapter),
                )
                results: list[Hit] = []
                for start_ch, start_v, end_ch, end_v, text in rows:
                    sc, sv = int(start_ch or 0), int(start_v or 0)
                    ec, ev = (int(end_ch) if end_ch is not None else None,
                              int(end_v) if end_v is not None else None)
                    if not _mybible_commentary_covers(ref, sc, sv, ec, ev):
                        continue
                    scope = ("Book introduction" if sc == 0 else
                             f"Chapter {sc}" if sv == 0 else f"{sc}:{sv}")
                    results.append(Hit(commentary, scope, str(text or ""), ref))
                    if len(results) >= limit:
                        break
                return results
        with _connect(commentary.path) as conn:
            table, cols = _resolve_schema(conn, "cmt")
            b = _require(cols, "Book")
            c = _require(cols, "Chapter")
            verse = _col(cols, "Verse", "FromVerse", "StartVerse")
            end = _col(cols, "ToVerse", "EndVerse", "LastVerse")
            content = _text_column("cmt", cols)
            where = f"{b}=? AND ({c}=? OR {c}=0)"
            if verse:
                v = _q(verse)
                if end:
                    where += f" AND ({c}=0 OR {v}=0 OR ({v}<=? AND {_q(end)}>=?))"
                    args = (ref.book, ref.chapter, ref.verse, ref.verse)
                else:
                    where += f" AND ({c}=0 OR {v}=0 OR {v}=?)"
                    args = (ref.book, ref.chapter, ref.verse)
            else:
                args = (ref.book, ref.chapter)
            rows = conn.execute(
                f"SELECT {c}, {(_q(verse) if verse else '0')}, {content} "
                f"FROM {_q(table)} WHERE {where} ORDER BY {c}, {(_q(verse) if verse else '0')} LIMIT ?",
                (*args, limit),
            ).fetchall()
            hits: list[Hit] = []
            for row in rows:
                ch = int(row[0] or 0)
                vn = int(row[1] or 0)
                scope = "Book introduction" if ch == 0 else "Chapter notes" if vn == 0 else f"Verse {vn}"
                hits.append(Hit(commentary, scope, str(row[2] or ""), ref))
            return hits

    def dictionary(self, dictionary: Module, term: str, limit: int = 10) -> list[Hit]:
        term = term.strip()
        if not term:
            return []
        with _connect(dictionary.path) as conn:
            table, cols = _resolve_schema(conn, "dct")
            word = _require(cols, "Word", "Topic", "Name", "Key")
            content = _text_column("dct", cols)
            variants = [term]
            if len(term) >= 2 and term[0].upper() in ("G", "H") and term[1:].isdigit():
                variants.extend((term[0].upper() + term[1:].zfill(4), term[0].upper() + str(int(term[1:]))))
            placeholders = ",".join("?" for _ in variants)
            rows = conn.execute(
                f"SELECT {word}, {content} FROM {_q(table)} "
                f"WHERE {word} COLLATE NOCASE IN ({placeholders}) LIMIT ?", (*variants, limit)
            ).fetchall()
            return [Hit(dictionary, str(r[0]), str(r[1] or "")) for r in rows]

    def topics(self, module: Module, term: str = "", limit: int = 300) -> list[str]:
        if module.kind not in ("bok", "jor"):
            return []
        with _connect(module.path) as conn:
            table, cols = _resolve_schema(conn, module.kind)
            title = _require(cols, "Topic", "Title", "Subject", "Name")
            hidden = _col(cols, "hidden")
            order = _col(cols, "relativeorder", "id")
            clauses: list[str] = []
            args: list[object] = []
            if term:
                clauses.append(f"{title} LIKE ? ESCAPE '\\'")
                args.append(_like(term))
            if hidden:
                clauses.append(f"({_q(hidden)}=0 OR {_q(hidden)} IS NULL)")
            sql = f"SELECT {title} FROM {_q(table)}"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += f" GROUP BY {title}"
            sql += f" ORDER BY {( 'MIN(' + _q(order) + '), ') if order else ''}{title} LIMIT ?"
            args.append(limit)
            return [str(r[0]) for r in conn.execute(sql, args) if r[0] is not None]

    def article(self, module: Module, topic: str) -> str | None:
        if module.kind not in ("bok", "jor"):
            return None
        with _connect(module.path) as conn:
            table, cols = _resolve_schema(conn, module.kind)
            title = _require(cols, "Topic", "Title", "Subject", "Name")
            row = conn.execute(
                f"SELECT {_text_column(module.kind, cols)} FROM {_q(table)} "
                f"WHERE {title}=? LIMIT 1", (topic,)
            ).fetchone()
            return str(row[0] or "") if row else None

    def resource(self, module: Module, name: str) -> bytes | None:
        """Return a module-local blob such as a map image, or None."""
        filename = (name or "").strip()
        if filename != filename.replace("\\", "/").rsplit("/", 1)[-1]:
            return None
        if not filename or filename in {".", ".."} or len(filename) > 120 or "." not in filename:
            return None
        if any(ch in filename for ch in '<>:"|?*'):
            return None
        with _connect(module.path) as conn:
            tables = _tables(conn)
            if "data" not in tables:
                return None
            cols = _columns(conn, tables["data"])
            key = _col(cols, "filename", "name", "id")
            blob = _col(cols, "content", "data", "file", "bytes")
            if not key or not blob:
                return None
            row = conn.execute(
                f"SELECT {_q(blob)} FROM {_q(tables['data'])} WHERE {_q(key)}=? LIMIT 1",
                (filename,),
            ).fetchone()
            payload = row[0] if row else None
            if isinstance(payload, memoryview):
                payload = payload.tobytes()
            return payload if isinstance(payload, bytes) else None

    def search_bible(self, bible: Module, phrase: str, limit: int = 200) -> list[Hit]:
        phrase = phrase.strip()
        if not phrase:
            return []
        if bible.format == "mybible":
            with _connect(bible.path) as conn:
                rows = conn.execute(
                    'SELECT book_number, chapter, verse, text FROM verses '
                    "WHERE text LIKE ? ESCAPE '\\' ORDER BY book_number, chapter, verse LIMIT ?",
                    (_like(phrase), limit),
                ).fetchall()
                result: list[Hit] = []
                for r in rows:
                    number = MYBIBLE_TO_BOOK.get(int(r[0]))
                    if number and int(r[1]) > 0 and int(r[2]) > 0:
                        reference = Reference(number, int(r[1]), int(r[2]))
                        result.append(Hit(bible, str(reference), str(r[3] or ""), reference))
                return result
        with _connect(bible.path) as conn:
            table, cols = _resolve_schema(conn, "bbl")
            b, c, v = (_require(cols, x) for x in ("Book", "Chapter", "Verse"))
            content = _text_column("bbl", cols)
            rows = conn.execute(
                f"SELECT {b}, {c}, {v}, {content} FROM {_q(table)} "
                f"WHERE {content} LIKE ? ESCAPE '\\' ORDER BY {b}, {c}, {v} LIMIT ?",
                (_like(phrase), limit),
            ).fetchall()
            return [Hit(bible, str(Reference(int(r[0]), int(r[1]), int(r[2]))), str(r[3] or ""),
                        Reference(int(r[0]), int(r[1]), int(r[2])))
                    for r in rows if 1 <= int(r[0]) <= 66 and int(r[1]) > 0 and int(r[2]) > 0]

    def search_library(self, phrase: str, limit: int = 200) -> list[Hit]:
        hits: list[Hit] = []
        phrase = phrase.strip()
        if not phrase:
            return hits
        for module in self.installed:
            if module.kind == "bbl":
                continue
            try:
                with _connect(module.path) as conn:
                    if module.format == "mybible" and module.kind == "cmt":
                        for r in conn.execute(
                            'SELECT book_number, chapter_number_from, verse_number_from, text '
                            "FROM commentaries WHERE text LIKE ? ESCAPE '\\' LIMIT ?",
                            (_like(phrase), limit - len(hits)),
                        ):
                            number = MYBIBLE_TO_BOOK.get(int(r[0])) if r[0] is not None else None
                            scope = (str(Reference(number, max(int(r[1] or 1), 1), max(int(r[2] or 1), 1)))
                                     if number else module.title)
                            hits.append(Hit(module, scope, str(r[3] or "")))
                        if len(hits) >= limit:
                            break
                        continue
                    table, cols = _resolve_schema(conn, module.kind)
                    content = _text_column(module.kind, cols)
                    title_col = _col(cols, "Word", "Topic", "Title", "Subject", "Name", "Key")
                    query = f"SELECT {(_q(title_col) if title_col else 'NULL')}, {content} FROM {_q(table)} "
                    query += f"WHERE {content} LIKE ? ESCAPE '\\' "
                    if title_col:
                        query += f"OR {_q(title_col)} LIKE ? ESCAPE '\\' "
                    query += "LIMIT ?"
                    args = (_like(phrase), _like(phrase), limit - len(hits)) if title_col else (_like(phrase), limit - len(hits))
                    for row in conn.execute(query, args):
                        title = str(row[0] or module.title)
                        hits.append(Hit(module, title, str(row[1] or "")))
            except (ModuleError, sqlite3.DatabaseError):
                continue
            if len(hits) >= limit:
                break
        return hits


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _mybible_commentary_covers(ref: Reference, sc: int, sv: int,
                               ec: int | None, ev: int | None) -> bool:
    """Match MyBible commentary ranges including book/chapter introductions."""
    if sc == 0:
        return True
    if ref.chapter < sc:
        return False
    if ec is None or ec == 0:
        if ref.chapter != sc:
            return False
        if sv == 0:
            return True
        return sv <= ref.verse <= (ev if ev is not None else sv)
    if (ref.chapter, ref.verse) < (sc, max(sv, 1)):
        return False
    return (ref.chapter, ref.verse) <= (ec, ev if ev is not None else 999)

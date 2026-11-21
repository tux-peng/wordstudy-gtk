"""User-owned notes/bookmarks, completely separate from MySword data."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager, closing
from pathlib import Path
from .references import Reference


@contextmanager
def _transaction(path: Path):
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            yield conn


class UserData:
    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "personal.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _transaction(self.path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS notes (
                    book INTEGER NOT NULL, chapter INTEGER NOT NULL, verse INTEGER NOT NULL,
                    body TEXT NOT NULL, modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (book, chapter, verse)
                );
                CREATE TABLE IF NOT EXISTS bookmarks (
                    book INTEGER NOT NULL, chapter INTEGER NOT NULL, verse INTEGER NOT NULL,
                    PRIMARY KEY (book, chapter, verse)
                );
            """)

    def get_note(self, ref: Reference) -> str:
        with _transaction(self.path) as conn:
            row = conn.execute("SELECT body FROM notes WHERE book=? AND chapter=? AND verse=?",
                               (ref.book, ref.chapter, ref.verse)).fetchone()
        return row[0] if row else ""

    def save_note(self, ref: Reference, body: str) -> None:
        with _transaction(self.path) as conn:
            if body.strip():
                conn.execute("""INSERT INTO notes(book,chapter,verse,body) VALUES(?,?,?,?)
                    ON CONFLICT(book,chapter,verse) DO UPDATE SET body=excluded.body, modified=CURRENT_TIMESTAMP""",
                    (ref.book, ref.chapter, ref.verse, body))
            else:
                conn.execute("DELETE FROM notes WHERE book=? AND chapter=? AND verse=?",
                             (ref.book, ref.chapter, ref.verse))

    def has_bookmark(self, ref: Reference) -> bool:
        with _transaction(self.path) as conn:
            return conn.execute("SELECT 1 FROM bookmarks WHERE book=? AND chapter=? AND verse=?",
                                (ref.book, ref.chapter, ref.verse)).fetchone() is not None

    def toggle_bookmark(self, ref: Reference) -> bool:
        exists = self.has_bookmark(ref)
        with _transaction(self.path) as conn:
            if exists:
                conn.execute("DELETE FROM bookmarks WHERE book=? AND chapter=? AND verse=?",
                             (ref.book, ref.chapter, ref.verse))
            else:
                conn.execute("INSERT INTO bookmarks(book,chapter,verse) VALUES(?,?,?)",
                             (ref.book, ref.chapter, ref.verse))
        return not exists

    def bookmarks(self) -> list[Reference]:
        with _transaction(self.path) as conn:
            return [Reference(*r) for r in conn.execute(
                "SELECT book,chapter,verse FROM bookmarks ORDER BY book,chapter,verse")]

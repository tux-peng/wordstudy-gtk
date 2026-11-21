"""Generate original demonstration study modules, not a complete Bible.

Usage:
  python3 -m wordstudy.sample             # creates ./demo_modules
  python3 -m wordstudy.sample --install   # also imports into personal library
"""
from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from .modules import Library


SAMPLES = {
    "demo.bbl.mybible": (
        "Bible",
        "CREATE TABLE Bible (Book INTEGER, Chapter INTEGER, Verse INTEGER, Scripture TEXT, PRIMARY KEY(Book,Chapter,Verse))",
        [
            (1, 1, 1, '<TS>The Creation<Ts>In the beginning God created the heaven and the earth. <WH430>'),
            (1, 1, 2, 'And the earth was without form, and void; and darkness was upon the face of the deep.'),
            (19, 23, 1, 'The LORD is my shepherd; I shall not want.'),
            (43, 3, 16, 'For God so <FR>loved<Fr> the world, that he gave his only begotten Son, '
                         'that whosoever believeth in him should not perish, but have everlasting life. <WG25>'),
            (43, 3, 17, 'For God sent not his Son into the world to condemn the world; '
                         'but that the world through him might be saved.'),
            (45, 8, 1, 'There is therefore now no condemnation to them which are in Christ Jesus.'),
        ],
    ),
    "demo.cmt.mybible": (
        "Commentary",
        "CREATE TABLE Commentary (Book INTEGER, Chapter INTEGER, FromVerse INTEGER, ToVerse INTEGER, Scripture TEXT)",
        [
            (43, 3, 16, 17, '<b>Study question:</b> What does this passage teach about the purpose of the Son’s coming? '
                              'Compare <a href="b43.3.17">John 3:17</a>.'),
            (1, 1, 1, 2, 'Observe the repeated pattern of creation described in this opening account.'),
            (19, 23, 1, 1, 'Reflect on the shepherd metaphor and how it shapes the meaning of the psalm.'),
            (45, 8, 1, 1, 'What is the significance of “no condemnation” in the surrounding argument?'),
        ],
    ),
    "demo.dct.mybible": (
        "Dictionary",
        "CREATE TABLE Dictionary (Word TEXT PRIMARY KEY, Definition TEXT)",
        [
            ("God", "A term used in Scripture for the divine being; examine the context for the intended meaning."),
            ("love", "A word frequently associated with devotion, self-giving, and care. See <a href='b43.3.16'>John 3:16</a>."),
            ("G25", "<b>agapaō</b> — to love; demonstration-only simplified Greek lexical entry."),
            ("H430", "<b>elohim</b> — a Hebrew word referring to God or gods depending on context."),
        ],
    ),
    "demo.bok.mybible": (
        "Book",
        "CREATE TABLE Book (Topic TEXT PRIMARY KEY, Content TEXT)",
        [
            ("How to study a passage", "<h3>Observation, interpretation, application</h3>"
             "<p>Read the whole chapter. Ask what is repeated, who is speaking, "
             "and what the original context was.</p><p>Compare multiple translations "
             "before reaching a conclusion.</p>"),
            ("Sample cross-reference", "<p>Open <a href='b43.3.16'>John 3:16</a> and observe its context.</p>"),
        ],
    ),
    "demo.jor.mybible": (
        "Journal",
        "CREATE TABLE Journal (Topic TEXT PRIMARY KEY, Content TEXT)",
        [("Study prompts", "Consider writing down an observation, a question, and a possible application."),],
    ),
}


def create_samples(destination: str | Path) -> list[Path]:
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    results = []
    for name, (table, schema, entries) in SAMPLES.items():
        path = target / name
        if path.exists():
            path.unlink()
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE Details (Description TEXT, Abbreviation TEXT, Language TEXT, RightToLeft INTEGER)")
            conn.execute("INSERT INTO Details VALUES (?,?,?,?)", (f"Demo {table} (sample only)", f"Demo{table}", "eng", 0))
            conn.execute(schema)
            placeholders = ",".join("?" * len(entries[0]))
            conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", entries)
            conn.commit()
        results.append(path)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Create five sample MySword-style modules")
    parser.add_argument("--install", action="store_true", help="Add samples to your WordStudy GTK library")
    parser.add_argument("--bundled", action="store_true", help="Use the bundled samples without recreating files")
    args = parser.parse_args()
    if args.bundled:
        location = Path(__file__).resolve().parent.parent / "demo_modules"
        paths = sorted(location.glob("demo.*.mybible"))
        if not paths:
            parser.error(f"No bundled demo files found at {location}")
        print(f"Using {len(paths)} bundled demonstration modules at {location}")
    else:
        paths = create_samples(Path.cwd() / "demo_modules")
        print(f"Created {len(paths)} demonstration modules in {Path.cwd() / 'demo_modules'}")
    if args.install:
        lib = Library()
        for path in paths:
            modules = lib.import_file(path)
            print("Imported:", ", ".join(m.label for m in modules))
        print("Launch with: python3 -m wordstudy")


if __name__ == "__main__":
    main()

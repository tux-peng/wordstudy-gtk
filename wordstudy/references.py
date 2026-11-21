"""Bible book order and robust human-friendly reference parsing."""
from __future__ import annotations

import re
from dataclasses import dataclass

BOOKS = (
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua",
    "Judges", "Ruth", "1 Samuel", "2 Samuel", "1 Kings", "2 Kings",
    "1 Chronicles", "2 Chronicles", "Ezra", "Nehemiah", "Esther", "Job",
    "Psalms", "Proverbs", "Ecclesiastes", "Song of Solomon", "Isaiah",
    "Jeremiah", "Lamentations", "Ezekiel", "Daniel", "Hosea", "Joel",
    "Amos", "Obadiah", "Jonah", "Micah", "Nahum", "Habakkuk",
    "Zephaniah", "Haggai", "Zechariah", "Malachi", "Matthew", "Mark",
    "Luke", "John", "Acts", "Romans", "1 Corinthians", "2 Corinthians",
    "Galatians", "Ephesians", "Philippians", "Colossians", "1 Thessalonians",
    "2 Thessalonians", "1 Timothy", "2 Timothy", "Titus", "Philemon",
    "Hebrews", "James", "1 Peter", "2 Peter", "1 John", "2 John",
    "3 John", "Jude", "Revelation",
)

# MyBible.zone uses PalmBible+-derived book IDs, not the consecutive 1–66
# ordinals used by MySword. These are the documented 66 canonical IDs.
MYBIBLE_BOOK_IDS = (
    10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140,
    150, 160, 190, 220, 230, 240, 250, 260, 290, 300, 310, 330,
    340, 350, 360, 370, 380, 390, 400, 410, 420, 430, 440, 450,
    460, 470, 480, 490, 500, 510, 520, 530, 540, 550, 560, 570,
    580, 590, 600, 610, 620, 630, 640, 650, 660, 670, 680, 690,
    700, 710, 720, 730,
)
MYBIBLE_TO_BOOK = {number: ordinal for ordinal, number in enumerate(MYBIBLE_BOOK_IDS, 1)}

ABBR = (
    "Gen", "Exod", "Lev", "Num", "Deut", "Josh", "Judg", "Ruth",
    "1Sam", "2Sam", "1Kgs", "2Kgs", "1Chr", "2Chr", "Ezra", "Neh",
    "Esth", "Job", "Ps", "Prov", "Eccl", "Song", "Isa", "Jer", "Lam",
    "Ezek", "Dan", "Hos", "Joel", "Amos", "Obad", "Jonah", "Mic",
    "Nah", "Hab", "Zeph", "Hag", "Zech", "Mal", "Matt", "Mark",
    "Luke", "John", "Acts", "Rom", "1Cor", "2Cor", "Gal", "Eph",
    "Phil", "Col", "1Thess", "2Thess", "1Tim", "2Tim", "Titus",
    "Phlm", "Heb", "Jas", "1Pet", "2Pet", "1John", "2John",
    "3John", "Jude", "Rev",
)

EXTRA = {
    "psalm": 19, "psa": 19, "pss": 19, "psalm s": 19,
    "canticles": 22, "song of songs": 22, "sos": 22,
    "jn": 43, "joh": 43, "mt": 40, "mk": 41, "lk": 42,
    "revelations": 66, "apocalypse": 66, "acts of the apostles": 44,
    "1 jn": 62, "2 jn": 63, "3 jn": 64,
}


def _key(value: str) -> str:
    value = value.strip().lower().replace(".", "")
    value = re.sub(r"\b(first|i)\b", "1", value)
    value = re.sub(r"\b(second|ii)\b", "2", value)
    value = re.sub(r"\b(third|iii)\b", "3", value)
    return re.sub(r"[^a-z0-9]", "", value)


LOOKUP: dict[str, int] = {}
for index, (book, abbr) in enumerate(zip(BOOKS, ABBR), start=1):
    LOOKUP[_key(book)] = index
    LOOKUP[_key(abbr)] = index
    if len(_key(book)) >= 4:
        LOOKUP.setdefault(_key(book)[:3], index)
for alias, index in EXTRA.items():
    LOOKUP[_key(alias)] = index


@dataclass(frozen=True)
class Reference:
    book: int
    chapter: int
    verse: int = 1

    def __str__(self) -> str:
        return f"{BOOKS[self.book - 1]} {self.chapter}:{self.verse}"


NUMERIC = re.compile(r"^([1-9]\d*)\.([1-9]\d*)(?:\.([1-9]\d*))?(?:-\d+)?$")
HUMAN = re.compile(r"^\s*(.+?)\s+(\d+)(?:\s*[:.]\s*(\d+))?(?:\s*-\s*\d+)?\s*$")


def parse_reference(raw: str) -> Reference:
    """Accept 'John 3:16', 'Jn 3', '1 Cor 13:4' or MySword '43.3.16'."""
    raw = raw.strip().removeprefix("#")
    if raw[:1].lower() == "b" and len(raw) > 1 and raw[1].isdigit():
        raw = raw[1:]
    match = NUMERIC.fullmatch(raw)
    if match:
        book, chapter, verse = (int(match[1]), int(match[2]), int(match[3] or 1))
    else:
        match = HUMAN.fullmatch(raw)
        if not match:
            raise ValueError("Enter a passage such as John 3:16 or 43.3.16")
        book = LOOKUP.get(_key(match[1]))
        if book is None:
            raise ValueError(f"Unknown Bible book: {match[1]}")
        chapter, verse = int(match[2]), int(match[3] or 1)
    if not (1 <= book <= 66 and 1 <= chapter <= 200 and 1 <= verse <= 200):
        raise ValueError("Reference is outside the supported 66-book numbering range")
    return Reference(book, chapter, verse)

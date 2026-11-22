"""Public MySword website catalog (no private Android API needed).

The official download pages link to getfile.php?file=<module>.mybible.gz.
Only links advertised on those pages are used; no URL guessing.
"""
from __future__ import annotations
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qs, unquote
import tempfile

from .module_downloader import DownloadError, MAX_DOWNLOAD, _read_url
from .modules import Library, Module, ModuleError

BASE = "https://www.mysword.info/download-mysword/"
CATEGORIES = {
    "bibles": "Bible", "commentaries": "Commentary",
    "dictionaries": "Dictionary", "books": "Book",
    "journals": "Journal", "graphics": "Map",
}
MAX_PAGE = 3 * 1024 * 1024

@dataclass(frozen=True)
class MySwordEntry:
    name: str
    language: str
    kind: str
    filename: str
    url: str

class _PageParser(HTMLParser):
    def __init__(self, category: str):
        super().__init__(convert_charrefs=True)
        self.category = category
        self.entries: list[MySwordEntry] = []
        self.language = ""
        self.heading = False
        self.heading_buffer: list[str] = []
        self.link = None
        self.link_buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("h2", "h3", "h4"):
            self.heading = True
            self.heading_buffer = []
        if tag == "a":
            href = attrs.get("href", "")
            if "getfile.php" in href and "file=" in href:
                self.link = urljoin(BASE, href)
                self.link_buffer = []

    def handle_data(self, data):
        if self.heading:
            self.heading_buffer.append(data)
        if self.link:
            self.link_buffer.append(data)

    def handle_endtag(self, tag):
        if tag in ("h2", "h3", "h4") and self.heading:
            label = " ".join("".join(self.heading_buffer).split())
            if label.lower().startswith("language:"):
                self.language = label.split(":", 1)[1].strip()
                # Strip the parenthetical count, while preserving names.
                import re
                self.language = re.sub(r"\s+\(\d+\)$", "", self.language)
            self.heading = False
        if tag == "a" and self.link:
            url = self.link
            title = " ".join("".join(self.link_buffer).split())
            self.link = None
            if not title:
                return
            query = parse_qs(urlsplit(url).query)
            filename = unquote(query.get("file", [""])[0])
            if (not filename or Path(filename).name != filename or
                    not filename.lower().endswith((".mybible", ".mybible.gz", ".zip"))):
                return
            if urlsplit(url).scheme not in ("http", "https"):
                return
            self.entries.append(MySwordEntry(title, self.language, self.category, filename, url))


def parse_mysword_page(page: bytes, kind: str) -> list[MySwordEntry]:
    parser = _PageParser(kind)
    parser.feed(page.decode("utf-8-sig", errors="replace"))
    # Some pages repeat the same link in separate sections.
    unique = {entry.url: entry for entry in parser.entries}
    return list(unique.values())


def fetch_mysword_catalog(category: str, language: str) -> tuple[list[MySwordEntry], list[str]]:
    """Fetch only a selected official category page, then filter by language.

    No network request occurs until the caller explicitly invokes this method.
    The site groups modules by language on each category page.
    """
    if category not in CATEGORIES:
        raise ValueError("Select a MySword module category")
    if not language:
        raise ValueError("Select a language")
    try:
        page = _read_url(BASE + category, MAX_PAGE)
        entries = parse_mysword_page(page, CATEGORIES[category])
    except (DownloadError, OSError, ValueError) as error:
        return [], [f"{category}: {error}"]
    selected = [entry for entry in entries
                if language == "all" or entry.language.casefold() == language.casefold()]
    return sorted(selected, key=lambda e: (e.language.casefold(), e.name.casefold())), []


def install_mysword(entry: MySwordEntry, library: Library) -> list[Module]:
    data = _read_url(entry.url, MAX_DOWNLOAD)
    with tempfile.TemporaryDirectory(prefix="wordstudy-mysword-") as folder:
        path = Path(folder) / entry.filename
        path.write_bytes(data)
        return library.import_file(path)

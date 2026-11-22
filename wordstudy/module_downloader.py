"""MyBible Android module registry reader and safe downloader."""
from __future__ import annotations
import io
import json
import re
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from .modules import Library, Module, ModuleError

# Endpoint used by BibleMultiConverter's MyBibleZoneListDownloader.
REGISTRY_URL = "http://myb.1gb.ru/registry.zip"
MAX_REGISTRY = 16 * 1024 * 1024
MAX_DOWNLOAD = 350 * 1024 * 1024
TYPE_MAP = {"": "Bible", "commentaries": "Commentary", "dictionary": "Dictionary"}

class DownloadError(Exception):
    pass

@dataclass(frozen=True)
class CatalogModule:
    abbreviation: str
    description: str
    language: str
    filename: str
    kind: str
    updated: str
    size: str
    urls: tuple[str, ...]


def _read_url(url: str, limit: int) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise DownloadError("Only HTTP(S) URLs are supported")
    request = urllib.request.Request(url, headers={"User-Agent": "WordStudy-GTK/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme not in ("https", "http"):
                raise DownloadError("Unsupported redirect")
            data = response.read(limit + 1)
    except OSError as exc:
        raise DownloadError(f"Network error: {exc}") from exc
    if len(data) > limit:
        raise DownloadError("Download exceeds size limit")
    return data


def parse_registry(payload: bytes) -> list[CatalogModule]:
    try:
        if zipfile.is_zipfile(io.BytesIO(payload)):
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                entries = [e for e in archive.infolist() if not e.is_dir() and e.filename.lower().endswith(".json")]
                if not entries:
                    raise DownloadError("Registry ZIP contains no JSON file")
                if entries[0].file_size > MAX_REGISTRY:
                    raise DownloadError("Registry JSON is too large")
                with archive.open(entries[0]) as f:
                    payload = f.read(MAX_REGISTRY + 1)
        if len(payload) > MAX_REGISTRY:
            raise DownloadError("Registry exceeds size limit")
        data = json.loads(payload.decode("utf-8-sig"))
    except (ValueError, UnicodeError, OSError, zipfile.BadZipFile) as exc:
        raise DownloadError(f"Invalid registry: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("downloads"), list):
        raise DownloadError("Registry has no downloads list")
    hosts = {host["alias"]: host["path"] for host in data.get("hosts", [])
             if isinstance(host, dict) and isinstance(host.get("alias"), str)
             and isinstance(host.get("path"), str) and "%s" in host["path"]}
    modules = []
    for item in data["downloads"]:
        if not isinstance(item, dict):
            continue
        filename = item.get("fil", "")
        abbreviation = str(item.get("abr") or "").replace(" (renamed)", "").strip()
        if not isinstance(filename, str) or Path(filename).name != filename or not abbreviation:
            continue
        # Registry 'fil' is a logical module identifier, not a SQLite filename.
        # The repository serves ZIP archives whose members contain SQLite modules.
        lower = filename.lower()
        if lower.endswith(".commentaries"):
            kind = "Commentary"
        elif lower.endswith(".dictionary"):
            kind = "Dictionary"
        elif "." not in filename or lower.endswith(".sqlite3"):
            kind = "Bible"
        else:
            # Plans, devotions, bundles, cross-references, etc. aren't yet supported.
            continue
        raw_urls = item.get("url", [])
        if isinstance(raw_urls, str):
            raw_urls = [raw_urls]
        if not isinstance(raw_urls, list):
            continue
        urls = []
        for link in raw_urls:
            if not isinstance(link, str):
                continue
            if link.startswith(("https://", "http://")):
                resolved = link
            else:
                match = re.fullmatch(r"\{([^}]+)\}(.*)", link)
                if not match or match.group(1) not in hosts:
                    continue
                name = match.group(2) or filename
                if not name or "/" in name or "\\" in name:
                    continue
                resolved = hosts[match.group(1)].replace("%s", urllib.parse.quote(urllib.parse.unquote(name), safe=""))
            if urllib.parse.urlsplit(resolved).scheme in ("http", "https") and resolved not in urls:
                urls.append(resolved)
        if urls:
            modules.append(CatalogModule(abbreviation, str(item.get("des") or abbreviation),
                           str(item.get("lng") or ""), filename + ".zip", kind,
                           str(item.get("upd") or ""), str(item.get("siz") or ""), tuple(urls)))
    return sorted(modules, key=lambda m: (m.kind, m.language, m.abbreviation.casefold()))


def fetch_catalog(url: str = REGISTRY_URL) -> list[CatalogModule]:
    return parse_registry(_read_url(url, MAX_REGISTRY))


def install_module(item: CatalogModule, library: Library) -> list[Module]:
    failures = []
    for url in item.urls:
        try:
            content = _read_url(url, MAX_DOWNLOAD)
            with tempfile.TemporaryDirectory(prefix="wordstudy-download-") as directory:
                path = Path(directory) / item.filename
                path.write_bytes(content)
                # Reuses the existing SQLite schema checks, archive limits and import pipeline.
                return library.import_file(path)
        except (DownloadError, ModuleError, OSError, zipfile.BadZipFile) as exc:
            failures.append(str(exc))
    raise DownloadError("No mirror succeeded: " + "; ".join(failures[:3]))

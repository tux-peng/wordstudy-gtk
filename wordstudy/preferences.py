"""Persistent, validated presentation preferences (independent of GTK)."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class DisplayPreferences:
    """Defaults match the original reader; fonts use Pango font descriptions."""

    bible_font: str = "Noto Serif 15"
    study_font: str = "Noto Serif 15"
    notes_font: str = "Sans 13"
    title_font: str = "Sans Bold 17"
    wrap_bible: bool = True
    wrap_study: bool = True
    wrap_notes: bool = True
    wrap_titles: bool = True
    show_strongs: bool = True
    show_morphology: bool = True
    words_of_christ_red: bool = True


def compact_module_name(abbreviation: str, title: str, limit: int = 38) -> str:
    """Bound combo-box labels even when a module supplies a huge title/abbr.

    The full name is kept in a tooltip and the article/reader pane. GTK combo
    boxes measure their entries, so clipping *only* the renderer is not enough
    to prevent imported text from setting the window's minimum width.
    """
    name = abbreviation.strip() if abbreviation and abbreviation.strip() else title.strip()
    if title and title.strip() and title.strip() != name:
        name += " — " + title.strip()
    if limit < 2:
        raise ValueError("limit must be at least 2")
    return name[:limit - 1] + "…" if len(name) > limit else name


class PreferencesStore:
    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "preferences.json"

    def load(self) -> DisplayPreferences:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return DisplayPreferences()
        if not isinstance(data, dict):
            return DisplayPreferences()
        defaults = DisplayPreferences()
        for field in fields(defaults):
            value = data.get(field.name)
            baseline = getattr(defaults, field.name)
            if isinstance(baseline, bool):
                if type(value) is bool:
                    setattr(defaults, field.name, value)
            elif isinstance(value, str) and 0 < len(value.strip()) <= 150:
                setattr(defaults, field.name, value.strip())
        return defaults

    def save(self, prefs: DisplayPreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".preferences-", suffix=".tmp", delete=False,
            ) as out:
                temp_path = out.name
                json.dump(asdict(prefs), out, ensure_ascii=False, indent=2)
                out.write("\n")
            os.replace(temp_path, self.path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

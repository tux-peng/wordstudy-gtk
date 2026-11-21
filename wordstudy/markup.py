"""Safe MySword HTML/GBF to styled text spans; no HTML/JavaScript execution."""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from .references import MYBIBLE_TO_BOOK


@dataclass(frozen=True)
class Span:
    text: str
    styles: tuple[str, ...] = ()
    link: str | None = None
    image: str | None = None


_START_END = (
    ("FI", "Fi", "i", "i"), ("FO", "Fo", "i", "i"),
    ("FU", "Fu", "u", "u"),
    ("TS", "Ts", "h3", "h3"),
    ("RF", "Rf", 'span class="footnote"', "span"),
    ("Q", "q", 'span class="interlinear"', "span"),
    ("E", "e", 'span class="translation"', "span"),
    ("T", "t", 'span class="translation"', "span"),
    ("X", "x", 'span class="transliteration"', "span"),
    ("H", "h", "span", "span"), ("G", "g", "span", "span"),
)


def _words_of_christ(value: str, enabled: bool) -> str:
    """Honor MyBible <J> and MySword GBF <FR> speech tags."""
    opening = '<span class="jesus">' if enabled else "<span>"
    value = re.sub(r"<J>", opening, value, flags=re.IGNORECASE)
    value = re.sub(r"</J>", "</span>", value, flags=re.IGNORECASE)
    # GBF opening is <FR>; closers are <Fr>, </Fr>, or </FR>.
    value = re.sub(r"<FR(?:\s+[^>]*)?>", opening, value)
    value = re.sub(r"</?Fr>", "</span>", value)
    value = re.sub(r"</FR>", "</span>", value)
    value = re.sub(r"<fr(?:\s+[^>]*)?>", opening, value)
    return re.sub(r"</fr>", "</span>", value)


def _safe_image_name(src: str) -> str | None:
    """Allow only a module-local image filename, never a URL or path."""
    name = (src or "").strip().replace("\\", "/").split("?", 1)[0]
    if not name or re.match(r"^(?:[a-z]+:|//)", name, flags=re.IGNORECASE):
        return None
    name = name.rsplit("/", 1)[-1]
    if name in {".", ".."} or len(name) > 120 or "." not in name:
        return None
    if re.fullmatch(r"[\w.-]+", name):
        return name
    return None


def _color_is_red(value: str | None) -> bool:
    if not value:
        return False
    raw = value.strip().lower().replace(" ", "")
    if raw in {"red", "maroon", "crimson", "firebrick", "darkred"}:
        return True
    if raw.startswith("rgb") and "255,0,0" in raw:
        return True
    hexv = raw[1:] if raw.startswith("#") else raw
    if re.fullmatch(r"[0-9a-f]{3}", hexv):
        hexv = "".join(ch * 2 for ch in hexv)
    if re.fullmatch(r"[0-9a-f]{6}", hexv):
        red, green, blue = int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16)
        return red >= 176 and green <= 96 and blue <= 96
    return False


def _internal_link(raw: str) -> str:
    """Map basic MyBible hyperlinks to existing internal reader destinations.

    Do not follow external links, scripts, file references, or unknown schemes.
    """
    href = raw.strip()
    if href.startswith("#"):
        href = href[1:].strip()
    if re.match(r"^[jJ](?:\s+|:-)", href):
        topic = re.sub(r"^[jJ](?:\s+|:-)", "", href, count=1).strip()
        return f"j-{topic}" if topic else ""
    if href[:2].upper() == 'B:':
        match = re.match(r'^B:(\d+)\s+(\d+):(\d+)', href, flags=re.IGNORECASE)
        if match:
            book = MYBIBLE_TO_BOOK.get(int(match[1]))
            if book:
                return f'b{book}.{int(match[2])}.{int(match[3])}'
        return ''
    if href[:2].upper() == 'S:':
        term = href[2:].strip()
        if re.fullmatch(r'[GH]\d{1,5}', term, flags=re.IGNORECASE):
            return 's' + term[0].upper() + term[1:]
        if re.fullmatch(r'[\w -]{1,90}', term):
            return 'd' + term
        return ''
    return href


def prepare_markup(text: str | None, bible: bool = False, *,
                   format: str = "mysword", show_strongs: bool = True,
                   show_morphology: bool = True,
                   words_of_christ_red: bool = True,
                   book: int | None = None) -> str:
    """Convert MySword GBF / MyBible verse tags into safe, styled text.

    Source format must be explicit.  MyBible <S>/<m>/<J> markup is not GBF.
    The underlying text is never changed in the installed SQLite module.
    """
    value = (text or "").replace("\x00", "")
    if not bible:
        return value
    if format == "mybible":
        # In MyBible, <S>123</S> is a Strong number (Hebrew in the OT),
        # <m>... </m> is morphology and <J>...</J> is Jesus' speech.
        prefix = "H" if book is not None and book <= 39 else "G"
        value = re.sub(
            r"<S>(\d{1,5})</S>",
            (lambda m: f' <a href="s{prefix}{m[1]}">{prefix}{m[1]}</a>')
            if show_strongs else (lambda m: ""),
            value, flags=re.IGNORECASE,
        )
        value = re.sub(
            r"<(?:m|M)>([^<>]{1,100})</(?:m|M)>",
            (lambda m: f' <a href="m{m[1]}">{m[1]}</a>')
            if show_morphology else (lambda m: ""), value,
        )
        value = _words_of_christ(value, words_of_christ_red)
        value = value.replace("<CL>", "\n")
        # MyBible <n>..</n> is a translator's note, not an HTML element.
        value = value.replace("<n>", '<span class="footnote">').replace("</n>", "</span>")
        return value
    # MySword's opening and closing GBF tags differ only in capitalization.
    for opening, closing, html_open, html_close in _START_END:
        value = re.sub(r"<" + opening + r"(?:\s+[^>]*)?>", f"<{html_open}>", value)
        value = value.replace(f"<{closing}>", f"</{html_close}>")
    value = re.sub(r"<TS\d+>", "<h3>", value)
    value = re.sub(r"<CM\s*/?>", "\n", value)
    value = re.sub(r"<CI\s*/?>", " ", value)
    value = re.sub(r"<(?:PI|PF)[0-7]>", "  ", value)
    value = re.sub(r"<W([GH])(\d+)>",
                   (lambda m: f' <a href="s{m[1]}{m[2]}">{m[1]}{m[2]}</a>')
                   if show_strongs else (lambda m: ""), value)
    value = re.sub(r"<WT([\w-]+)>",
                   (lambda m: f' <a href="m{m[1]}">{m[1]}</a>')
                   if show_morphology else (lambda m: ""), value)
    value = re.sub(r"<RX(\d+\.\d+\.\d+(?:-\d+)?)>", lambda m: f' <a href="b{m[1]}">↗</a>', value)
    value = re.sub(r"<(?:D|wh|wg|wt)\s*/?>", "", value)
    # Speech tags are converted after GBF so MySword <FR> and MyBible <J> both work.
    return _words_of_christ(value, words_of_christ_red)


class _SpanParser(HTMLParser):
    BLOCKS = frozenset(("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "blockquote"))
    STYLE_MAP = {
        "b": "bold", "strong": "bold", "i": "italic", "em": "italic",
        "u": "underline", "h1": "heading", "h2": "heading", "h3": "heading",
        "h4": "bold", "h5": "bold", "h6": "bold", "code": "code",
    }

    def __init__(self, words_of_christ_red: bool = True) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None, str | None]] = []
        self.spans: list[Span] = []
        self.hidden = 0
        self.words_of_christ_red = words_of_christ_red

    def _emit(self, content: str) -> None:
        if not content or self.hidden:
            return
        styles: list[str] = []
        link: str | None = None
        for _, style, href in self.stack:
            if style and style not in styles:
                styles.append(style)
            if href:
                link = href
        self.spans.append(Span(content, tuple(styles), link))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        if tag in ("script", "style", "iframe", "object"):
            self.hidden += 1
        if tag == "br":
            self._emit("\n")
            return
        if tag == "hr":
            self._emit("\n────────────\n")
            return
        if tag in ("img", "input", "meta", "link", "source", "area", "wbr"):
            if tag == "img" and not self.hidden:
                image = _safe_image_name(attributes.get("src") or "")
                if image:
                    self.spans.append(Span("", image=image))
            return
        if tag in self.BLOCKS:
            if self.spans and not self.spans[-1].text.endswith("\n"):
                self._emit("\n")
            if tag == "li":
                self._emit(" • ")
        style = self.STYLE_MAP.get(tag)
        classes = (attributes.get("class") or "").lower().split()
        style_color = None
        match = re.search(r"(?:^|;)\s*color\s*:\s*([^;]+)", attributes.get("style") or "", flags=re.I)
        if match:
            style_color = match.group(1)
        if self.words_of_christ_red and (
            "jesus" in classes or "red" in classes or "woj" in classes
            or _color_is_red(attributes.get("color")) or _color_is_red(style_color)
        ):
            style = "red"
        elif "footnote" in classes:
            style = "muted"
        elif "transliteration" in classes:
            style = "italic"
        elif "blue" in classes:
            style = "link"
        href = _internal_link(attributes.get("href") or "")
        # Only internal MySword links are actionable; no external URIs or scripts.
        if tag != "a" or not href or not re.match(r"^(?:s[GH]\d+$|b(?:\d+\.\d+\.\d+|[1-3]?\s?[A-Za-z])|d(?:-[^ ]+ |[A-Za-z0-9])|c(?:-[^ ]+ |\d)|m[\w-]+$|k-|j-|r\S)", href):
            href = None
        if href and not style:
            style = "link"
        self.stack.append((tag, style, href))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in ("br", "img", "hr", "input", "meta", "link", "source", "area", "wbr"):
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("script", "style", "iframe", "object"):
            self.hidden = max(0, self.hidden - 1)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in self.BLOCKS:
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        self._emit(data)


def styled_spans(value: str | None, bible: bool = False, **options: object) -> list[Span]:
    parser = _SpanParser(words_of_christ_red=bool(options.get("words_of_christ_red", True)))
    try:
        parser.feed(prepare_markup(value, bible=bible, **options))
        parser.close()
    except (ValueError, AssertionError):
        return [Span(value or "")]
    return parser.spans


def plain_text(value: str | None, bible: bool = False, **options: object) -> str:
    return "".join(span.text for span in styled_spans(value, bible, **options))

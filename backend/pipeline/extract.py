"""Read a book file (PDF, EPUB, or TXT) into plain text.

Every format is turned into the same shape, an `ExtractedBook`:
- title and author (from the file's metadata, or the filename and "Unknown")
- a list of `Section`s, each with a title and a list of paragraphs
- the table of contents entries, if the file has them

Sections come from the file's own structure when it has one:
- PDF: its bookmarks (the outline shown in a PDF viewer's sidebar)
- EPUB: its table of contents, matched to the files in reading order
- TXT: no structure, so the whole book is one untitled section

The chunker (chunking.py) decides what to do with these sections.
"""

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from ebooklib import ITEM_DOCUMENT, epub

SUPPORTED_TYPES = ("pdf", "epub", "txt")
WORDS_PER_PAGE = 400
# A real text PDF has a few hundred words per page. Below this, the pages are
# probably scanned images, which we can't read without OCR.
MIN_WORDS_PER_PDF_PAGE = 50

# EPUB files are XHTML; reading them with the HTML parser is fine.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


class ExtractionError(Exception):
    """The file can't be read. The message is safe to show to the user."""


def count_words(text: str) -> int:
    # English-only for now, so splitting on whitespace is good enough.
    return len(text.split())


def clean_text(text: str) -> str:
    """Join a paragraph's lines into one line and tidy the spaces."""
    text = text.replace("\xad", "")  # invisible "soft" hyphens
    # A word broken across two lines: "exam-\nple" -> "example".
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    return " ".join(text.split())


@dataclass
class Section:
    title: str  # "" when we don't know the title
    paragraphs: list[str] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return sum(count_words(p) for p in self.paragraphs)


@dataclass
class ExtractedBook:
    title: str
    author: str
    file_type: str  # "pdf", "epub", or "txt"
    sections: list[Section]
    toc: list[str] = field(default_factory=list)  # table of contents titles
    page_count: int | None = None  # real pages; PDF only

    @property
    def paragraphs(self) -> list[str]:
        return [p for s in self.sections for p in s.paragraphs]

    @property
    def word_count(self) -> int:
        return sum(s.word_count for s in self.sections)

    @property
    def page_estimate(self) -> int:
        return max(1, round(self.word_count / WORDS_PER_PAGE))

    @property
    def has_structure(self) -> bool:
        """True when the file itself told us where (at least two) chapters start."""
        return sum(1 for s in self.sections if s.title) >= 2

    def first_words(self, n: int) -> str:
        """The opening n words, keeping paragraph breaks (for the book check)."""
        out, total = [], 0
        for p in self.paragraphs:
            words = p.split()
            if total + len(words) > n:
                out.append(" ".join(words[: n - total]))
                break
            out.append(p)
            total += len(words)
        return "\n\n".join(out)


# ---------------------------------------------------------------- checks


def check_file_size(path: Path, max_mb: int) -> None:
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > max_mb:
        raise ExtractionError(
            f"This file is {size_mb:.0f} MB. The limit is {max_mb} MB."
        )


def book_status(book: ExtractedBook, max_words: int) -> str:
    """'ok', 'too_long', or 'scanned_not_supported' (the job status names)."""
    if book.file_type == "pdf" and book.page_count:
        if book.word_count / book.page_count < MIN_WORDS_PER_PDF_PAGE:
            return "scanned_not_supported"
    if book.word_count > max_words:
        return "too_long"
    return "ok"


# ---------------------------------------------------------------- entry point


def extract_book(path: str | Path) -> ExtractedBook:
    path = Path(path)
    file_type = path.suffix.lower().lstrip(".")
    if file_type not in SUPPORTED_TYPES:
        raise ExtractionError(
            f"'.{file_type}' files are not supported. Please use PDF, EPUB, or TXT."
        )
    if file_type == "pdf":
        book = _extract_pdf(path)
    elif file_type == "epub":
        book = _extract_epub(path)
    else:
        book = _extract_txt(path)

    # Drop sections that ended up with no text at all.
    book.sections = [s for s in book.sections if s.paragraphs]
    return book


def _title_from_filename(path: Path) -> str:
    return path.stem.replace("_", " ").replace("-", " ").strip() or "Untitled"


# ---------------------------------------------------------------- PDF


def _extract_pdf(path: Path) -> ExtractedBook:
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        raise ExtractionError("Could not open this PDF. Is the file damaged?") from e
    if doc.needs_pass:
        raise ExtractionError("This PDF is password-protected. Please remove the password.")

    with doc:
        meta = doc.metadata or {}
        # One list of paragraphs per page. PyMuPDF splits a page into "blocks";
        # in normal books one block is one paragraph (or a heading).
        pages: list[list[str]] = []
        for page in doc:
            paragraphs = []
            for block in page.get_text("blocks"):
                if block[6] != 0:  # 0 = text block, 1 = image
                    continue
                text = clean_text(block[4])
                if text and not text.isdigit():  # skip bare page numbers
                    paragraphs.append(text)
            pages.append(paragraphs)
        outline = doc.get_toc()  # [[level, title, page (1-based)], ...]

    bookmarks = _chapter_bookmarks(outline, len(pages))
    if bookmarks:
        sections = _split_pages_by_bookmarks(pages, bookmarks)
    else:
        sections = [Section("", [p for page in pages for p in page])]

    return ExtractedBook(
        title=(meta.get("title") or "").strip() or _title_from_filename(path),
        author=(meta.get("author") or "").strip() or "Unknown",
        file_type="pdf",
        sections=sections,
        toc=[title.strip() for _, title, _ in outline if title.strip()],
        page_count=len(pages),
    )


def _chapter_bookmarks(outline: list, page_count: int) -> list[tuple[str, int]]:
    """Pick the bookmarks that mark chapters, as (title, 0-based page) pairs.

    Bookmarks are nested (part > chapter > heading). We use the highest level
    that has at least two entries. For example, if the only top-level bookmark
    is the book's own title, we use the level below it.
    """
    levels = sorted({level for level, _, _ in outline})
    for level in levels:
        marks = [
            (title.strip(), page - 1)
            for lvl, title, page in outline
            if lvl == level and 1 <= page <= page_count
        ]
        if len(marks) >= 2:
            return sorted(marks, key=lambda m: m[1])
    return []


def _split_pages_by_bookmarks(
    pages: list[list[str]], bookmarks: list[tuple[str, int]]
) -> list[Section]:
    # Splits at page boundaries. If a chapter starts halfway down a page, the
    # top of that page goes to the new chapter. Good enough for summaries.
    sections = []
    first_page = bookmarks[0][1]
    if first_page > 0:  # pages before the first bookmark (cover, copyright...)
        sections.append(Section("", [p for page in pages[:first_page] for p in page]))
    for i, (title, start) in enumerate(bookmarks):
        end = bookmarks[i + 1][1] if i + 1 < len(bookmarks) else len(pages)
        sections.append(Section(title, [p for page in pages[start:end] for p in page]))
    return sections


# ---------------------------------------------------------------- EPUB

# HTML tags that hold a paragraph-sized piece of text.
_BLOCK_TAGS = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "dd", "dt"]


def _extract_epub(path: Path) -> ExtractedBook:
    try:
        book = epub.read_epub(str(path))
    except Exception as e:
        raise ExtractionError("Could not open this EPUB. Is the file damaged?") from e

    def first_meta(name: str) -> str:
        values = book.get_metadata("DC", name)
        return values[0][0].strip() if values and values[0][0] else ""

    toc_entries = _flatten_epub_toc(book.toc)
    # Map each file to the title(s) of the table of contents entries pointing at it.
    titles_by_file: dict[str, list[str]] = {}
    for title, href in toc_entries:
        file_name = href.split("#")[0]
        titles = titles_by_file.setdefault(file_name, [])
        if title not in titles:
            titles.append(title)

    sections: list[Section] = []
    # The spine is the reading order: a list of (item id, linear) pairs.
    for item_id, _ in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ITEM_DOCUMENT:
            continue
        # Skip the EPUB's own table of contents page.
        if isinstance(item, epub.EpubNav) or "nav" in getattr(item, "properties", []):
            continue
        paragraphs = _html_paragraphs(item.get_content())
        title = " / ".join(titles_by_file.get(item.get_name(), []))
        if not title and sections and sections[-1].title:
            # A file with no TOC entry after a chapter is usually the rest of
            # that chapter (long chapters are often split into several files).
            sections[-1].paragraphs.extend(paragraphs)
        else:
            sections.append(Section(title, paragraphs))

    return ExtractedBook(
        title=first_meta("title") or _title_from_filename(path),
        author=first_meta("creator") or "Unknown",
        file_type="epub",
        sections=sections,
        toc=[title for title, _ in toc_entries],
    )


def _flatten_epub_toc(toc) -> list[tuple[str, str]]:
    """EPUB TOCs are nested lists of Links and (Section, children) pairs."""
    out = []
    for entry in toc:
        if isinstance(entry, tuple):  # (Section or Link, [children])
            parent, children = entry
            if getattr(parent, "href", None) and parent.title:
                out.append((parent.title.strip(), parent.href))
            out.extend(_flatten_epub_toc(children))
        elif getattr(entry, "href", None) and entry.title:
            out.append((entry.title.strip(), entry.href))
    return out


def _html_paragraphs(html: bytes) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    # Take the outermost block tags only, so a <p> inside a <blockquote>
    # isn't counted twice.
    blocks = [
        tag for tag in body.find_all(_BLOCK_TAGS)
        if tag.find_parent(_BLOCK_TAGS) is None
    ]
    paragraphs = [clean_text(tag.get_text(" ")) for tag in blocks]
    paragraphs = [p for p in paragraphs if p]

    # Some books put text straight inside <div>s with no <p> tags. If the
    # block tags missed most of the text, fall back to splitting on lines.
    all_text = body.get_text("\n")
    if sum(count_words(p) for p in paragraphs) < 0.5 * count_words(all_text):
        paragraphs = [clean_text(line) for line in all_text.split("\n")]
        paragraphs = [p for p in paragraphs if p]
    return paragraphs


# ---------------------------------------------------------------- TXT


def _extract_txt(path: Path) -> ExtractedBook:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")  # utf-8-sig also removes a BOM if there is one
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")  # common for older Windows files
    text = text.replace("\r\n", "\n")

    # Paragraphs are usually separated by blank lines; the lines inside a
    # paragraph are joined back together.
    paragraphs = [clean_text(p) for p in re.split(r"\n\s*\n", text)]
    paragraphs = [p for p in paragraphs if p]
    # Some files have no blank lines at all (one paragraph per line). Then the
    # split above gives a few giant "paragraphs", so split on lines instead.
    if paragraphs and sum(count_words(p) for p in paragraphs) / len(paragraphs) > 300:
        paragraphs = [clean_text(line) for line in text.split("\n")]
        paragraphs = [p for p in paragraphs if p]

    return ExtractedBook(
        title=_title_from_filename(path),
        author="Unknown",
        file_type="txt",
        sections=[Section("", paragraphs)],
    )

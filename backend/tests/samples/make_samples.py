"""Build small sample books (PDF, EPUB, TXT) for the tests and for trying the CLI.

The tests call `make_all(folder)` to build fresh copies in a temporary folder.
You can also run it yourself to get files to try:

    python -m tests.samples.make_samples          # writes into tests/samples/

Every sample has the same short "book": a copyright page, then three
chapters. Chapter 2 is much longer than the others, so the chunker has to
split it.
"""

import sys
from pathlib import Path

import pymupdf
from ebooklib import epub

TITLE = "The Little Lighthouse"
AUTHOR = "Ada Example"

COPYRIGHT = ["Copyright 2026 Ada Example. All rights reserved."]


def _paragraphs(chapter: int, count: int) -> list[str]:
    # 23 words per paragraph, so word counts in the tests are easy to predict.
    return [
        f"In chapter {chapter} paragraph {i} the keeper climbed the old stairs "
        f"and watched the grey sea roll slowly toward the quiet harbour town."
        for i in range(1, count + 1)
    ]


# (title, paragraphs). Words in each chapter's paragraphs: 230, 1380, 115.
CHAPTERS = [
    ("Chapter 1: The Storm", _paragraphs(1, 10)),
    ("Chapter 2: The Long Night", _paragraphs(2, 60)),
    ("Chapter 3: Morning", _paragraphs(3, 5)),
]
CHAPTER_WORDS = [230, 1380, 115]  # not counting the chapter heading


def make_pdf(path: Path) -> None:
    """A PDF with bookmarks (an outline), one chapter starting on each new page."""
    doc = pymupdf.open()
    doc.set_metadata({"title": TITLE, "author": AUTHOR})
    toc = []

    def new_page():
        return doc.new_page(width=420, height=600)

    page = new_page()
    page.insert_text((40, 60), COPYRIGHT[0], fontsize=9)

    for title, paragraphs in CHAPTERS:
        page = new_page()
        toc.append([1, title, page.number + 1])  # bookmark pages are 1-based
        page.insert_text((40, 60), title, fontsize=14)
        y = 90
        for para in paragraphs:
            # Each paragraph is its own text box with a gap after it, so
            # PyMuPDF reads it back as one "block".
            rect = pymupdf.Rect(40, y, 380, y + 60)
            if rect.y1 > 560:
                page = new_page()
                y = 60
                rect = pymupdf.Rect(40, y, 380, y + 60)
            page.insert_textbox(rect, para, fontsize=10)
            y += 70
    doc.set_toc(toc)
    doc.save(path)


def make_epub(path: Path) -> None:
    """An EPUB with a table of contents and one file per chapter."""
    book = epub.EpubBook()
    book.set_identifier("kru-sample-1")
    book.set_title(TITLE)
    book.set_language("en")
    book.add_author(AUTHOR)

    copyright_page = epub.EpubHtml(title="Copyright", file_name="copyright.xhtml")
    copyright_page.content = f"<html><body><p>{COPYRIGHT[0]}</p></body></html>"
    book.add_item(copyright_page)

    chapter_items = []
    for n, (title, paragraphs) in enumerate(CHAPTERS, start=1):
        item = epub.EpubHtml(title=title, file_name=f"chapter{n}.xhtml")
        body = "".join(f"<p>{p}</p>" for p in paragraphs)
        item.content = f"<html><body><h1>{title}</h1>{body}</body></html>"
        book.add_item(item)
        chapter_items.append(item)

    book.toc = [epub.Link("copyright.xhtml", "Copyright", "copyright")] + [
        epub.Link(item.file_name, title, f"ch{n}")
        for n, (item, (title, _)) in enumerate(zip(chapter_items, CHAPTERS), start=1)
    ]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", copyright_page] + chapter_items
    epub.write_epub(str(path), book)


def make_txt(path: Path) -> None:
    """Plain text: paragraphs separated by blank lines, no structure at all."""
    parts = [TITLE, AUTHOR, COPYRIGHT[0]]
    for title, paragraphs in CHAPTERS:
        parts.append(title)
        parts.extend(paragraphs)
    path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")


def make_all(folder: Path) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = {
        "pdf": folder / "sample.pdf",
        "epub": folder / "sample.epub",
        "txt": folder / "sample.txt",
    }
    make_pdf(paths["pdf"])
    make_epub(paths["epub"])
    make_txt(paths["txt"])
    return paths


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    for kind, p in make_all(out).items():
        print(f"{kind}: {p}")

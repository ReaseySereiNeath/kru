import pymupdf
import pytest
from ebooklib import epub

from pipeline.extract import (
    ExtractedBook,
    ExtractionError,
    Section,
    _chapter_bookmarks,
    book_status,
    check_file_size,
    clean_text,
    extract_book,
)
from tests.samples.make_samples import AUTHOR, CHAPTER_WORDS, CHAPTERS, TITLE

CHAPTER_TITLES = [title for title, _ in CHAPTERS]


def body_words(section):
    """Words in a chapter, not counting its heading line."""
    return section.word_count - len(section.title.split())


# ---------------------------------------------------------------- PDF


def test_pdf_metadata_and_pages(samples):
    book = extract_book(samples["pdf"])
    assert book.title == TITLE
    assert book.author == AUTHOR
    assert book.file_type == "pdf"
    assert book.page_count == 13
    assert book.toc == CHAPTER_TITLES


def test_pdf_chapters_come_from_bookmarks(samples):
    book = extract_book(samples["pdf"])
    assert book.has_structure
    # Untitled front matter (the copyright page), then the three chapters.
    assert [s.title for s in book.sections] == [""] + CHAPTER_TITLES
    assert [body_words(s) for s in book.sections[1:]] == CHAPTER_WORDS


def test_pdf_paragraphs_are_whole(samples):
    book = extract_book(samples["pdf"])
    first = book.sections[1].paragraphs[1]
    # The paragraph was wrapped over two lines in the PDF; it comes back as one.
    assert first == CHAPTERS[0][1][0]


def test_pdf_without_bookmarks_is_one_section(tmp_path):
    path = tmp_path / "plain.pdf"
    doc = pymupdf.open()
    for i in range(2):
        doc.new_page().insert_text((50, 72), f"Page {i} " + "word " * 100)
    doc.save(path)
    book = extract_book(path)
    assert len(book.sections) == 1
    assert book.sections[0].title == ""
    assert not book.has_structure
    assert book.title == "plain"  # no metadata, so the filename
    assert book.author == "Unknown"


def test_bookmarks_use_the_first_level_with_two_entries():
    outline = [[1, "My Book", 1], [2, "One", 2], [2, "Two", 5], [3, "Detail", 5]]
    assert _chapter_bookmarks(outline, page_count=10) == [("One", 1), ("Two", 4)]


def test_bookmarks_pointing_outside_the_book_are_ignored():
    outline = [[1, "One", 1], [1, "Broken", 0], [1, "Two", 3]]
    assert _chapter_bookmarks(outline, page_count=5) == [("One", 0), ("Two", 2)]


# ---------------------------------------------------------------- EPUB


def test_epub_metadata(samples):
    book = extract_book(samples["epub"])
    assert book.title == TITLE
    assert book.author == AUTHOR
    assert book.file_type == "epub"
    assert book.page_count is None
    assert book.toc == ["Copyright"] + CHAPTER_TITLES


def test_epub_chapters_follow_toc_and_skip_nav_page(samples):
    book = extract_book(samples["epub"])
    assert [s.title for s in book.sections] == ["Copyright"] + CHAPTER_TITLES
    assert [body_words(s) for s in book.sections[1:]] == CHAPTER_WORDS


def test_epub_file_without_toc_entry_continues_previous_chapter(tmp_path):
    book = epub.EpubBook()
    book.set_identifier("x")
    book.set_title("Split")
    items = []
    for name, text in [("a.xhtml", "First half."), ("a2.xhtml", "Second half."),
                       ("b.xhtml", "Next chapter.")]:
        item = epub.EpubHtml(file_name=name)
        item.content = f"<html><body><p>{text}</p></body></html>"
        book.add_item(item)
        items.append(item)
    book.toc = [epub.Link("a.xhtml", "One", "one"), epub.Link("b.xhtml", "Two", "two")]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = items
    path = tmp_path / "split.epub"
    epub.write_epub(str(path), book)

    sections = extract_book(path).sections
    assert [(s.title, s.paragraphs) for s in sections] == [
        ("One", ["First half.", "Second half."]),
        ("Two", ["Next chapter."]),
    ]


# ---------------------------------------------------------------- TXT


def test_txt(samples):
    book = extract_book(samples["txt"])
    assert book.title == "sample"
    assert book.author == "Unknown"
    assert len(book.sections) == 1
    # Title + author + copyright line + 3 headings + chapter text.
    assert book.word_count == 3 + 2 + 7 + sum(len(t.split()) for t in CHAPTER_TITLES) + sum(CHAPTER_WORDS)


def test_txt_joins_wrapped_lines_into_paragraphs(tmp_path):
    path = tmp_path / "wrapped.txt"
    path.write_text("One line\nwrapped here.\n\nSecond para.\n", encoding="utf-8")
    assert extract_book(path).paragraphs == ["One line wrapped here.", "Second para."]


def test_txt_without_blank_lines_uses_one_paragraph_per_line(tmp_path):
    path = tmp_path / "lines.txt"
    path.write_text("\n".join(f"Line {i} " + "word " * 20 for i in range(30)), encoding="utf-8")
    book = extract_book(path)
    assert len(book.paragraphs) == 30


def test_txt_old_windows_encoding(tmp_path):
    path = tmp_path / "old.txt"
    path.write_bytes("Caf\xe9 au lait".encode("cp1252"))
    assert extract_book(path).paragraphs == ["Café au lait"]


# ---------------------------------------------------------------- shared


def test_unsupported_file_type(tmp_path):
    path = tmp_path / "book.docx"
    path.write_text("hi")
    with pytest.raises(ExtractionError, match="not supported"):
        extract_book(path)


def test_damaged_pdf(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_text("this is not a pdf")
    with pytest.raises(ExtractionError):
        extract_book(path)


def test_file_size_limit(tmp_path):
    path = tmp_path / "big.txt"
    path.write_bytes(b"x" * (2 * 1024 * 1024))
    check_file_size(path, max_mb=3)  # fine
    with pytest.raises(ExtractionError, match="limit is 1 MB"):
        check_file_size(path, max_mb=1)


def test_clean_text():
    assert clean_text("exam-\nple  text\nhere") == "example text here"
    assert clean_text("soft\xadhyphen") == "softhyphen"
    assert clean_text("  \n ") == ""


def test_word_count_and_page_estimate():
    book = ExtractedBook("T", "A", "txt", [Section("", ["word " * 1000, "word " * 200])])
    assert book.word_count == 1200
    assert book.page_estimate == 3  # 1200 / 400


def test_first_words_keeps_paragraphs():
    book = ExtractedBook("T", "A", "txt", [Section("", ["a b c", "d e f", "g h"])])
    assert book.first_words(5) == "a b c\n\nd e"


def test_status_ok_and_too_long():
    book = ExtractedBook("T", "A", "txt", [Section("", ["word " * 500])])
    assert book_status(book, max_words=1000) == "ok"
    assert book_status(book, max_words=499) == "too_long"


def test_status_scanned_pdf(tmp_path):
    path = tmp_path / "scanned.pdf"
    doc = pymupdf.open()
    for _ in range(10):
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(50, 50, 300, 400), fill=(0.5, 0.5, 0.5))  # an "image"
    doc[0].insert_text((50, 30), "Only a few words here")
    doc.save(path)
    book = extract_book(path)
    assert book_status(book, max_words=200000) == "scanned_not_supported"


def test_real_text_pdf_is_not_scanned(samples):
    assert book_status(extract_book(samples["pdf"]), max_words=200000) == "ok"

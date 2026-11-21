"""Text out of every format, the way it reads aloud."""
import pytest

from conftest import extract
import samples

read = extract.read_document


def test_plain_text_paragraphs_and_hyphenation():
    text = ("The first paragraph has a compli-\ncated word\nand goes on.\n\n"
            "   12   \n\nThe second paragraph.\r\n\r\nThird, after Windows lines.")
    doc = read(text.encode("utf-8"), "notes.txt")
    assert doc.paragraphs == ["The first paragraph has a complicated word and goes on.",
                              "The second paragraph.", "Third, after Windows lines."]
    assert doc.title is None


def test_text_that_is_not_utf8():
    doc = read("Ærø og Højby".encode("cp1252"), "steder.txt")
    assert doc.paragraphs == ["Ærø og Højby"]


def test_markdown():
    md = ("# Holiday Plans\n\nWe go to **Skagen** in [July](https://x).\n\n"
          "## Packing\n\n- towels\n- `sunscreen`\n\n```\ncode = 1\n```\n\n"
          "| a | b |\n|---|---|\n\n> A quote\n> continues.\n")
    doc = read(md.encode(), "plans.md")
    assert doc.title == "Holiday Plans"
    assert doc.paragraphs == ["Holiday Plans", "We go to Skagen in July.", "Packing",
                              "towels", "sunscreen", "A quote continues."]


def test_pdf():
    data = samples.pdf(["Once upon a time there was a", "little mermaid.", "", "2"],
                       title="The Little Mermaid", author="H. C. Andersen")
    doc = read(data, "mermaid.pdf")
    assert doc.title == "The Little Mermaid"
    assert doc.author == "H. C. Andersen"
    assert " ".join(doc.paragraphs).startswith("Once upon a time there was a little mermaid.")
    assert "2" not in " ".join(doc.paragraphs).split()  # the page number


def test_word():
    data = samples.docx(["First paragraph.", "", "Second paragraph."], title="Minutes", author="Sissel")
    doc = read(data, "minutes.docx")
    assert (doc.title, doc.author) == ("Minutes", "Sissel")
    assert doc.paragraphs == ["First paragraph.", "Second paragraph."]  # no table, no empty one


def test_opendocument():
    data = samples.odt(["First.", "Second <text:span>with a span</text:span>."], title="Odt Title")
    doc = read(data, "file.odt")
    assert doc.title == "Odt Title"
    assert doc.paragraphs == ["Heading One", "First.", "Second with a span."]


def test_epub_follows_the_spine():
    data = samples.epub([["One a.", "One b."], ["Two a."]], title="A Book", author="Someone")
    doc = read(data, "book.epub")
    assert (doc.title, doc.author) == ("A Book", "Someone")
    assert doc.paragraphs == ["Chapter 1", "One a.", "One b.", "Chapter 2", "Two a."]


def test_html_reads_the_text_not_the_page():
    html = ("<html><head><title>Page Title</title><meta name='author' content='Me'>"
            "<style>p{}</style></head><body><nav><p>Menu</p></nav><h1>Head</h1>"
            "<p>A <b>bold</b>, word<br>and a line.</p><ul><li><p>Item</p></li></ul>"
            "<pre>code</pre><script>x()</script><footer><p>Foot</p></footer></body></html>")
    doc = read(html.encode(), "page.html")
    assert (doc.title, doc.author) == ("Page Title", "Me")
    assert doc.paragraphs == ["Head", "A bold, word and a line.", "Item"]


def test_title_from_name():
    assert extract.title_from_name("min_rapport-2024.pdf") == "min rapport 2024"
    assert extract.title_from_name("https://x.dk/a/Ferie%20planer.docx?dl=1") == "Ferie%20planer"


@pytest.mark.parametrize("name, ok", [("a.PDF", True), ("a.docx", True), ("a.doc", False),
                                      ("a.txt?x=1", True), ("a", False)])
def test_supported(name, ok):
    assert extract.is_supported(name) is ok


def test_a_broken_document_says_so():
    with pytest.raises(extract.DocumentError):
        read(b"not a pdf at all", "broken.pdf")
    with pytest.raises(extract.DocumentError):
        read(b"whatever", "file.xyz")

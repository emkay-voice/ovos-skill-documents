"""Finding documents in folders and at urls, and what each is called."""
import os
import time
from unittest.mock import patch

import pytest

from conftest import extract, library
import samples

Library = library.Library


def no_fetch(url):
    raise AssertionError(f"fetched {url}")


@pytest.fixture
def folder(tmp_path):
    docs = tmp_path / "docs"
    (docs / "sub").mkdir(parents=True)
    (docs / "plans.md").write_text("# Holiday Plans\n\nSkagen in July.")
    (docs / "sub" / "minutes.docx").write_bytes(samples.docx(["Hello."], title="Board Minutes", author="Sissel"))
    (docs / "my_old-notes.txt").write_text("Just text.")
    (docs / "picture.jpg").write_bytes(b"\xff\xd8")
    (docs / ".hidden.txt").write_text("no")
    (docs / "~$lock.docx").write_bytes(b"office lock file")
    return docs


def titles(lib):
    return {e.name: (e.title, e.author) for e in lib.entries.values()}


def test_a_folder_and_its_subfolders(tmp_path, folder):
    lib = Library(tmp_path / "library.json", fetch=no_fetch)
    assert lib.scan([str(folder)], []) is True
    assert titles(lib) == {"plans.md": ("Holiday Plans", ""),
                           "minutes.docx": ("Board Minutes", "Sissel"),
                           "my_old-notes.txt": ("my old notes", "")}


def test_a_folder_that_is_not_there_is_no_documents(tmp_path):
    lib = Library(tmp_path / "library.json", fetch=no_fetch)
    lib.scan([str(tmp_path / "nowhere"), "~/surely/not/here"], [])
    assert lib.entries == {}


def test_documents_are_opened_only_when_new_or_changed(tmp_path, folder):
    lib = Library(tmp_path / "library.json", fetch=no_fetch)
    lib.scan([str(folder)], [])
    with patch.object(library, "read_document", side_effect=AssertionError("opened again")):
        assert lib.scan([str(folder)], []) is False
        # a new Library on the same file remembers too (it is a restart)
        Library(tmp_path / "library.json", fetch=no_fetch).scan([str(folder)], [])
    time.sleep(0.01)
    (folder / "plans.md").write_text("# Summer Plans\n\nChanged.")
    os.utime(folder / "plans.md", (time.time() + 5, time.time() + 5))
    assert lib.scan([str(folder)], []) is True
    assert titles(lib)["plans.md"] == ("Summer Plans", "")


def test_the_users_title_wins_and_is_kept(tmp_path, folder):
    lib = Library(tmp_path / "library.json", fetch=no_fetch)
    lib.scan([str(folder)], [])
    doc_id = next(e.id for e in lib.entries.values() if e.name == "minutes.docx")
    assert lib.rename(doc_id, "  Mødereferat   marts ")
    assert lib.entries[doc_id].title == "Mødereferat marts"
    again = Library(tmp_path / "library.json", fetch=no_fetch)
    again.scan([str(folder)], [])
    assert again.entries[doc_id].title == "Mødereferat marts"
    assert again.entries[doc_id].custom_title == "Mødereferat marts"
    assert again.rename(doc_id, "")  # back to the document's own title
    assert again.entries[doc_id].title == "Board Minutes"
    assert not again.rename("no-such-id", "x")


def test_newest_first(tmp_path, folder):
    os.utime(folder / "plans.md", (time.time() + 100, time.time() + 100))
    lib = Library(tmp_path / "library.json", fetch=no_fetch)
    lib.scan([str(folder)], [])
    assert lib.newest().name == "plans.md"


def test_a_url_to_a_document_is_not_fetched_to_list_it(tmp_path):
    lib = Library(tmp_path / "library.json", fetch=no_fetch)
    lib.scan([], ["https://example.com/papers/Annual%20Letter.pdf"])
    [entry] = lib.entries.values()
    assert (entry.kind, entry.name, entry.title, entry.host) == \
        ("url", "Annual Letter.pdf", "Annual Letter", "example.com")


def test_a_folder_listing_at_a_url(tmp_path):
    listing = (b"<html><body><h1>Index of /docs</h1><a href='../'>Parent</a>"
               b"<a href='a.pdf'>a.pdf</a><a href='sub/Guide.docx'>The Guide</a>"
               b"<a href='image.png'>image.png</a><a href='a.pdf'>again</a></body></html>")
    lib = Library(tmp_path / "library.json", fetch=lambda url: (listing, "text/html"))
    lib.scan([], ["https://files.example.com/docs/"])
    assert sorted((e.source, e.title) for e in lib.entries.values()) == [
        ("https://files.example.com/docs/a.pdf", "a"),
        ("https://files.example.com/docs/sub/Guide.docx", "The Guide")]


def test_a_plain_list_of_urls(tmp_path):
    text = b"# my reading\nhttps://x.org/one.txt\nnot a url\nhttps://x.org/two.html\n"
    lib = Library(tmp_path / "library.json", fetch=lambda url: (text, "text/plain"))
    lib.scan([], ["https://x.org/list.txt.list"])
    assert sorted(e.name for e in lib.entries.values()) == ["one.txt", "two.html"]


def test_a_url_that_cannot_be_fetched_is_skipped(tmp_path, folder):
    def fail(url):
        raise OSError("offline")
    lib = Library(tmp_path / "library.json", fetch=fail)
    lib.scan([str(folder)], ["https://down.example.com/"])
    assert len(lib.entries) == 3


def test_reading_a_url_document_learns_its_title(tmp_path):
    pdf = samples.pdf(["Text."], title="Real Title", author="Writer")
    lib = Library(tmp_path / "library.json", fetch=lambda url: (pdf, "application/pdf"))
    lib.scan([], ["https://x.org/scan0001.pdf"])
    [entry] = lib.entries.values()
    assert entry.title == "scan0001"
    lib.read(entry.id)
    assert (lib.entries[entry.id].title, lib.entries[entry.id].author) == ("Real Title", "Writer")
    again = Library(tmp_path / "library.json", fetch=no_fetch)
    again.scan([], ["https://x.org/scan0001.pdf"])
    assert again.entries[entry.id].title == "Real Title"


def test_ids_are_stable(tmp_path, folder):
    a = Library(tmp_path / "a.json", fetch=no_fetch)
    b = Library(tmp_path / "b.json", fetch=no_fetch)
    a.scan([str(folder)], [])
    b.scan([str(folder)], [])
    assert set(a.entries) == set(b.entries)


def test_the_file_lists_the_documents_for_a_panel_page(tmp_path, folder):
    import json
    lib = Library(tmp_path / "library.json", fetch=no_fetch)
    lib.scan([str(folder)], [])
    saved = json.loads((tmp_path / "library.json").read_text())
    assert {d["name"] for d in saved["documents"]} == {"plans.md", "minutes.docx", "my_old-notes.txt"}

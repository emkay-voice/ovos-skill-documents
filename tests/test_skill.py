"""The skill on a bus: the reading protocol, its words, and the messages a
control-panel page would use."""
import threading
import time
from unittest.mock import MagicMock

import pytest
from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus

from conftest import skill_module
import samples

DocumentsSkill = skill_module.DocumentsSkill


class FakeFileSystem:
    def __init__(self, path):
        self.path = str(path)


class Recorder:
    def __init__(self, bus, *topics):
        self.messages = []
        for topic in topics:
            bus.on(topic, self.messages.append)

    def of(self, topic):
        return [m for m in self.messages if m.msg_type == topic]


def make_skill(tmp_path, monkeypatch, settings=None, langs=("en-us", "da-dk")):
    bus = FakeBus()
    s = DocumentsSkill.__new__(DocumentsSkill)
    s.log = MagicMock()
    s.skill_id = "ovos-skill-documents.test"
    s._bus = bus
    s._settings = dict(settings or {})
    s.file_system = FakeFileSystem(tmp_path / "skill")
    s.add_event = lambda topic, handler, *a, **k: bus.on(topic, handler)
    s.schedule_repeating_event = MagicMock()
    monkeypatch.setattr(DocumentsSkill, "lang", "en-us", raising=False)
    monkeypatch.setattr(DocumentsSkill, "native_langs", list(langs), raising=False)
    monkeypatch.setattr(DocumentsSkill, "settings", property(lambda self: self._settings), raising=False)
    s.initialize()
    return s, bus


@pytest.fixture
def docs(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "plans.md").write_text("# Holiday Plans\n\nWe go to Skagen.\n\nIn July.")
    (folder / "minutes.pdf").write_bytes(samples.pdf(["The board met.", "", "All agreed."],
                                                    title="Board Minutes March", author="Sissel"))
    (folder / "x.txt").write_text("One word title.")
    return folder


@pytest.fixture
def skill(tmp_path, monkeypatch, docs):
    return make_skill(tmp_path, monkeypatch, {"folders": str(docs)})


def search(bus, **data):
    seen = Recorder(bus, skill_module.COMMON_READING_SEARCH_RESPONSE)
    bus.emit(Message(skill_module.COMMON_READING_SEARCH, dict({"lang": "en-US"}, **data)))
    return [m.data for m in seen.of(skill_module.COMMON_READING_SEARCH_RESPONSE)]


def fetch(skill, bus, content_id):
    seen = Recorder(bus, skill_module.COMMON_READING_FETCH_CONTENT_RESPONSE)
    bus.emit(Message(f"{skill_module.COMMON_READING_FETCH_CONTENT}.{skill.skill_id}",
                     {"content_id": content_id}))
    [reply] = seen.of(skill_module.COMMON_READING_FETCH_CONTENT_RESPONSE)
    return reply.data["paragraphs"]


def test_a_title_is_found_and_read(skill):
    s, bus = skill
    [found] = search(bus, phrase="board minutes march", content_type="document")
    assert (found["title"], found["author"]) == ("Board Minutes March", "Sissel")
    assert found["confidence"] > 0.9 and found["source"] is None and found["machine_translated"] is False
    # a PDF seldom marks its paragraphs; the pipeline splits the sentences
    assert " ".join(fetch(s, bus, found["content_id"])) == "The board met. All agreed."


def test_no_title_reads_the_newest(skill, docs):
    s, bus = skill
    import os
    os.utime(docs / "plans.md", (time.time() + 60, time.time() + 60))
    s.rescan()
    [found] = search(bus, content_type="document")
    assert found["title"] == "Holiday Plans" and found["confidence"] == skill_module.NO_TITLE_CONFIDENCE
    [named] = search(bus, collection_hint="my documents")
    assert named["confidence"] == 1.0


def test_other_kinds_and_collections_are_not_ours(skill):
    _, bus = skill
    assert search(bus, phrase="board minutes", content_type="story") == []
    assert search(bus, phrase="board minutes", collection_hint="brothers grimm") == []
    assert search(bus, phrase="completely unrelated words here") == []


def test_danish_words_and_requests(skill):
    _, bus = skill
    assert search(bus, phrase="board minutes march", collection_hint="mine dokumenter", lang="da-DK")


def test_a_language_not_configured_is_not_served(skill):
    _, bus = skill
    assert search(bus, phrase="board minutes march", lang="ja-JP") == []


def test_vocabulary(skill):
    s, bus = skill
    seen = Recorder(bus, skill_module.COMMON_READING_VOCABULARY)
    bus.emit(Message(skill_module.COMMON_READING_VOCABULARY_GET, {"langs": ["da"]}))
    [da] = [m.data for m in seen.of(skill_module.COMMON_READING_VOCABULARY)]
    assert da["lang"] == "da"
    assert "dokumentet" in da["content_types"]["document"]
    assert "mine dokumenter" in da["collections"]
    # titles of two words or more: "x" (from x.txt) is left out
    assert da["titles"] == ["Board Minutes March", "Holiday Plans"]
    for words in da["content_types"]["document"] + da["collections"]:
        assert "rapport" not in words and "note" not in words


def test_the_text_is_read_once(skill, monkeypatch):
    s, bus = skill
    [found] = search(bus, phrase="holiday plans")
    first = fetch(s, bus, found["content_id"])
    monkeypatch.setattr(s.library, "read", MagicMock(side_effect=AssertionError("read again")))
    assert fetch(s, bus, found["content_id"]) == first == ["Holiday Plans", "We go to Skagen.", "In July."]


def test_the_text_is_started_on_while_the_pipeline_announces_it(skill, monkeypatch):
    s, bus = skill
    started = threading.Event()
    real = s.library.read
    monkeypatch.setattr(s.library, "read", lambda doc_id: (started.set(), real(doc_id))[1])
    search(bus, phrase="board minutes march")
    assert started.wait(2)


def test_a_document_that_cannot_be_read_gives_no_text(tmp_path, monkeypatch, docs):
    (docs / "broken.pdf").write_bytes(b"not really a pdf")
    s, bus = make_skill(tmp_path, monkeypatch, {"folders": str(docs)})
    [entry] = [e for e in s.library.entries.values() if e.name == "broken.pdf"]
    assert fetch(s, bus, entry.id) == []
    assert fetch(s, bus, "no-such-document") == []


def test_ping(skill):
    _, bus = skill
    seen = Recorder(bus, skill_module.COMMON_READING_PONG)
    bus.emit(Message(skill_module.COMMON_READING_PING, {"lang": "en-US"}))
    assert len(seen.of(skill_module.COMMON_READING_PONG)) == 1


def test_the_upload_folder_is_always_read(tmp_path, monkeypatch):
    s, bus = make_skill(tmp_path, monkeypatch)
    (s.upload_folder / "Uploaded Letter.txt").write_text("Dear all.")
    s.rescan()
    assert [e.title for e in s.library.entries.values()] == ["Uploaded Letter"]


# --- for a control-panel page ---------------------------------------------------------------

def ask(bus, topic, data=None):
    seen = Recorder(bus, f"{topic}.response")
    bus.emit(Message(topic, data or {}))
    [reply] = seen.of(f"{topic}.response")
    return reply.data


def test_list(skill, docs):
    s, bus = skill
    data = ask(bus, skill_module.DOCUMENTS_LIST)
    assert {d["title"] for d in data["documents"]} == {"Holiday Plans", "Board Minutes March", "x"}
    assert data["upload_folder"] == str(s.upload_folder)
    assert data["folders"] == [str(docs)] and ".pdf" in data["formats"]


def test_rename_changes_the_title_and_the_words(skill):
    s, bus = skill
    doc_id = next(e.id for e in s.library.entries.values() if e.name == "x.txt")
    vocab = Recorder(bus, skill_module.COMMON_READING_VOCABULARY)
    assert ask(bus, skill_module.DOCUMENTS_RENAME, {"id": doc_id, "title": "Grocery List"}) == {"ok": True}
    assert any("Grocery List" in m.data["titles"] for m in vocab.of(skill_module.COMMON_READING_VOCABULARY))
    [found] = search(bus, phrase="grocery list")
    assert found["content_id"] == doc_id
    assert ask(bus, skill_module.DOCUMENTS_RENAME, {"id": "nope", "title": "x"}) == {"ok": False}


def test_rescan_finds_a_new_document(skill, docs):
    s, bus = skill
    (docs / "New Story.txt").write_text("Once.")
    data = ask(bus, skill_module.DOCUMENTS_RESCAN)
    assert "New Story" in {d["title"] for d in data["documents"]}


def test_sources_are_saved_and_read_at_once(skill, docs, tmp_path):
    s, bus = skill
    other = tmp_path / "more"
    other.mkdir()
    (other / "letter.txt").write_text("Dear Emkay, hello.")
    data = ask(bus, skill_module.DOCUMENTS_SOURCES, {"folders": [str(docs), str(other)],
                                                     "urls": "https://example.com/a.pdf\n"})
    assert data["folders"] == [str(docs), str(other)]
    assert data["urls"] == ["https://example.com/a.pdf"]
    assert s.settings["folders"] == f"{docs}\n{other}"
    assert "letter" in {d["title"] for d in data["documents"]}


def test_sources_leave_out_what_was_not_sent(skill, docs):
    s, bus = skill
    ask(bus, skill_module.DOCUMENTS_SOURCES, {"urls": ["https://example.com/b.pdf"]})
    assert s.settings["folders"] == str(docs)
    assert s.settings["urls"] == "https://example.com/b.pdf"

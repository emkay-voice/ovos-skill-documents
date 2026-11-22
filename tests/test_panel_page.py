"""The page this skill has in ovos-control-panel, described in
controlpanel.json: the file, its strings in every language, and the skill's
answers to the page over the bus."""
import base64
import json
import re
import uuid
from pathlib import Path

import pytest
from ovos_bus_client.message import Message

import samples
from conftest import skill_module  # noqa: F401 - loads the package
from test_skill import docs, make_skill  # noqa: F401 - docs is a fixture

import ovos_skill_documents as pkg

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "controlpanel.json").read_text(encoding="utf-8")
PAGE = json.loads(TEXT)


def test_the_page_names_files_that_exist_and_its_own_messages():
    assert PAGE["controlpanel"] == 1 and PAGE["id"] == "documents" and PAGE["group"] == "library"
    assert (ROOT / PAGE["icon"]).is_file()
    sent = re.findall(r'"(?:query|send)": "([^"]+)"', TEXT)
    assert sent and all(m.startswith(PAGE["bus"]) for m in sent)
    upload = next(b for b in PAGE["blocks"] if b["type"] == "upload")
    assert set(upload["accept"]) == pkg.SUPPORTED and upload["max_mb"] * 1024 * 1024 == pkg.MAX_UPLOAD


def test_the_page_speaks_every_language_the_skill_does():
    files = {p.parent.name: json.loads(p.read_text(encoding="utf-8"))
             for p in (ROOT / "locale").glob("*/controlpanel.json")}
    assert set(files) == {p.name for p in (ROOT / "locale").iterdir() if p.is_dir()}
    keys = set(files["en-us"])
    for lang, table in files.items():
        assert set(table) == keys, lang
        assert all(text.strip() for text in table.values()), lang
    used = set(re.findall(r'"((?:documents|nav)\.[\w.]+)"', TEXT))
    errors = {f"documents.err.{e}" for e in ("kind", "upload", "notmine", "url")}
    assert used | errors | {"documents.up.true"} == keys, "every string is used, and every one used is there"


def test_the_panel_reads_it():
    described = pytest.importorskip("ovos_webui.described")
    page, refs = described.check(PAGE, "ovos-skill-documents")
    assert [b["type"] for b in page["blocks"]] == ["text", "upload", "text", "list", "actions", "form", "text"]
    assert "b1.upload" in refs and refs["b3.a0"]["confirm"] and not refs["b3.rename"]["privileged"]


@pytest.mark.parametrize("name, safe", [
    ("../../etc/passwd.txt", "passwd.txt"), ("..hidden.md", "hidden.md"),
    ("a/b\\c:d.pdf", "b_c_d.pdf"), ("", "document"), ("Bog om Æbler.epub", "Bog om Æbler.epub"),
])
def test_an_uploaded_file_name_stays_in_its_folder(name, safe):
    assert pkg.safe_name(name) == safe


@pytest.fixture
def bus_skill(tmp_path, monkeypatch, docs):  # noqa: F811 - the fixture
    return make_skill(tmp_path, monkeypatch, {"folders": str(docs)})


def ask(bus, msg_type, **data):
    return bus.wait_for_response(Message(msg_type, data), timeout=5).data


def upload(bus, name, blob, title="", size=4):
    """As the control panel sends a file: in parts, the last answer the answer."""
    uid, parts = uuid.uuid4().hex, max(1, -(-len(blob) // size))
    answer = None
    for n in range(parts):
        answer = ask(bus, pkg.DOCUMENTS_UPLOAD, upload=uid, name=name, part=n, parts=parts, title=title,
                     data=base64.b64encode(blob[n * size:(n + 1) * size]).decode())
    return answer


def titles(library):
    return sorted(d["title"] for d in library["documents"])


def test_the_page_lists_what_the_skill_found(bus_skill):
    skill, bus = bus_skill
    library = ask(bus, pkg.DOCUMENTS_LIST)
    assert "Board Minutes March" in titles(library) and not any(d["uploaded"] for d in library["documents"])
    assert library["folders"] and ".pdf" in library["formats"]


def test_an_upload_lands_in_the_upload_folder_with_its_title(bus_skill):
    skill, bus = bus_skill
    blob = samples.pdf(["Chapter one.", "", "It was dark."], title="Untitled")
    doc = upload(bus, "story.pdf", blob, title="The Dark Story", size=1000)["document"]
    assert doc["title"] == "The Dark Story" and doc["custom_title"] == "The Dark Story" and doc["uploaded"]
    assert Path(doc["source"]).parent == skill.upload_folder and doc["name"] == "story.pdf"
    again = upload(bus, "story.pdf", blob, size=100000)["document"]
    assert again["name"] == "story (2).pdf", "a second upload does not overwrite the first"
    assert not skill._uploads, "nothing is left waiting"


@pytest.mark.parametrize("name, blob, error", [("song.mp3", b"ID3", "documents.err.kind"),
                                               ("empty.txt", b"", "documents.err.upload")])
def test_an_upload_it_cannot_read_is_refused(bus_skill, name, blob, error):
    skill, bus = bus_skill
    assert upload(bus, name, blob) == {"error": error}
    assert not list(skill.upload_folder.iterdir())


def test_parts_that_do_not_fit_together_are_refused(bus_skill):
    skill, bus = bus_skill
    uid = uuid.uuid4().hex
    first = ask(bus, pkg.DOCUMENTS_UPLOAD, upload=uid, name="a.txt", part=0, parts=2, data="aGk=")
    assert first == {"ok": True, "received": 1}
    assert ask(bus, pkg.DOCUMENTS_UPLOAD, upload=uid, name="a.txt", part=1, parts=3, data="aGk=") \
        == {"error": "documents.err.upload"}
    assert not skill._uploads and not list(skill.upload_folder.iterdir())


def test_a_title_is_given_and_taken_back(bus_skill):
    _, bus = bus_skill
    doc = next(d for d in ask(bus, pkg.DOCUMENTS_LIST)["documents"] if d["name"] == "plans.md")
    assert ask(bus, pkg.DOCUMENTS_RENAME, id=doc["id"], title="Summer") == {"ok": True}
    assert "Summer" in titles(ask(bus, pkg.DOCUMENTS_LIST))
    ask(bus, pkg.DOCUMENTS_RENAME, id=doc["id"], title="")
    assert "Holiday Plans" in titles(ask(bus, pkg.DOCUMENTS_LIST))


def test_only_uploaded_documents_can_be_deleted(bus_skill, docs):  # noqa: F811
    skill, bus = bus_skill
    doc = upload(bus, "note.txt", b"Remember the milk.")["document"]
    library = ask(bus, pkg.DOCUMENTS_DELETE, id=doc["id"])
    assert doc["id"] not in {d["id"] for d in library["documents"]}
    assert not (skill.upload_folder / "note.txt").exists()
    own = next(d for d in library["documents"] if d["name"] == "plans.md")
    assert ask(bus, pkg.DOCUMENTS_DELETE, id=own["id"]) == {"error": "documents.err.notmine"}
    assert (docs / "plans.md").exists()


def test_the_folders_and_urls_are_set_from_the_page(bus_skill, tmp_path):
    skill, bus = bus_skill
    more = tmp_path / "more"
    more.mkdir()
    (more / "letter.txt").write_text("Dear Sissel, the meeting is on Monday.")
    library = ask(bus, pkg.DOCUMENTS_SOURCES, folders=[str(more), " "], urls=[])
    assert library["folders"] == [str(more)] and skill.settings["folders"] == str(more)
    assert {d["name"] for d in library["documents"]} == {"letter.txt"}
    assert ask(bus, pkg.DOCUMENTS_SOURCES, urls=["file:///etc/passwd"]) == {"error": "documents.err.url"}

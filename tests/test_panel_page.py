"""The page this skill adds to ovos-control-panel: its description, its
strings, and its routes talking to the skill over a bus."""
import json
import re
from pathlib import Path

import pytest

import samples
from conftest import skill_module  # noqa: F401 - loads the package
from test_skill import docs, make_skill  # noqa: F401 - docs is a fixture

from ovos_skill_documents import panel

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "panel"
LANGS = {p.name.split("-")[0] for p in (ROOT / "locale").iterdir() if p.is_dir()}


def test_the_page_description_names_files_that_exist():
    page = panel.PAGE
    assert page["api_version"] == 1 and page["id"] == "documents"
    for key in ("html", "icon"):
        assert (PANEL / page[key]).is_file(), key
    assert callable(page["routes"])
    assert page["group"] == panel.LIBRARY["id"] and panel.LIBRARY in page["groups"]
    [(path, size)] = page["uploads"].items()
    assert path == "/api/documents/upload" and panel.MAX_UPLOAD < size <= 64 * 1024 * 1024


def test_the_page_speaks_every_language_the_skill_does():
    files = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (PANEL / "i18n").glob("*.json")}
    assert LANGS <= set(files)
    keys = set(files["en"])
    for lang, table in files.items():
        assert set(table) == keys, lang
        assert all(text.strip() for text in table.values()), lang
    html = (PANEL / "page.html").read_text(encoding="utf-8")
    used = set(re.findall(r'data-i18n="([^"]+)"', html)) | set(re.findall(r'\bt\("([^"]+)"', html))
    shell = {k for k in used if k.startswith("file.")}
    assert used - shell <= keys, "a string the page uses has no translation"
    assert keys - used <= {"nav.group.library", "documents.title"}, "a translation nothing uses"


@pytest.mark.parametrize("name, safe", [
    ("../../etc/passwd.txt", "passwd.txt"), ("..hidden.md", "hidden.md"),
    ("a/b\\c:d.pdf", "b_c_d.pdf"), ("", "document"), ("Bog om Æbler.epub", "Bog om Æbler.epub"),
])
def test_an_uploaded_file_name_stays_in_its_folder(name, safe):
    from ovos_skill_documents.panel import api
    assert api.safe_name(name) == safe


# ── the routes, against the skill itself on a bus ────────────────────────────
fastapi = pytest.importorskip("fastapi")
pytest.importorskip("multipart")


@pytest.fixture
def client(tmp_path, monkeypatch, docs):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    skill, bus = make_skill(tmp_path, monkeypatch, {"folders": str(docs)})

    class R:
        api = APIRouter(prefix="/api")
        privileged = APIRouter(prefix="/api")
        config = None

        @staticmethod
        def need_bus():
            return bus

    panel.routes(R)
    app = FastAPI()
    app.include_router(R.api)
    app.include_router(R.privileged)
    with TestClient(app) as c:
        yield c, skill


def titles(library):
    return sorted(d["title"] for d in library["documents"])


def test_the_page_lists_what_the_skill_found(client):
    c, skill = client
    library = c.get("/api/documents").json()
    assert "Board Minutes March" in titles(library)
    assert library["upload_folder"] == str(skill.upload_folder) and ".pdf" in library["formats"]


def test_an_upload_lands_in_the_upload_folder_with_its_title(client):
    c, skill = client
    blob = samples.pdf(["Chapter one.", "", "It was dark."], title="Untitled")
    r = c.post("/api/documents/upload", files={"file": ("../story.pdf", blob, "application/pdf")},
               data={"title": "The Dark Story"})
    assert r.status_code == 200, r.text
    doc = r.json()["document"]
    assert doc["title"] == "The Dark Story" and doc["custom_title"] == "The Dark Story"
    assert Path(doc["source"]).parent == skill.upload_folder and doc["name"] == "story.pdf"
    again = c.post("/api/documents/upload", files={"file": ("story.pdf", blob, "application/pdf")}).json()
    assert again["document"]["name"] == "story (2).pdf", "a second upload does not overwrite the first"


@pytest.mark.parametrize("name, blob, status", [("song.mp3", b"ID3", 415), ("empty.txt", b"", 400)])
def test_an_upload_it_cannot_read_is_refused(client, name, blob, status):
    c, skill = client
    assert c.post("/api/documents/upload", files={"file": (name, blob)}).status_code == status
    assert not list(skill.upload_folder.iterdir())


def test_a_title_is_given_and_taken_back(client):
    c, _ = client
    doc = next(d for d in c.get("/api/documents").json()["documents"] if d["name"] == "plans.md")
    assert c.post("/api/documents/rename", json={"id": doc["id"], "title": "Summer"}).json() == {"ok": True}
    assert "Summer" in titles(c.get("/api/documents").json())
    c.post("/api/documents/rename", json={"id": doc["id"], "title": ""})
    assert "Holiday Plans" in titles(c.get("/api/documents").json())
    assert c.post("/api/documents/rename", json={"id": "nope", "title": "x"}).status_code == 404


def test_only_uploaded_documents_can_be_deleted(client, docs):
    c, skill = client
    doc = c.post("/api/documents/upload", files={"file": ("note.txt", b"Remember the milk.")}).json()["document"]
    library = c.post("/api/documents/delete", json={"id": doc["id"]}).json()
    assert doc["id"] not in {d["id"] for d in library["documents"]}
    assert not (skill.upload_folder / "note.txt").exists()
    own = next(d for d in library["documents"] if d["name"] == "plans.md")
    assert c.post("/api/documents/delete", json={"id": own["id"]}).status_code == 403
    assert (docs / "plans.md").exists()


def test_the_folders_and_urls_are_set_from_the_page(client, tmp_path):
    c, skill = client
    more = tmp_path / "more"
    more.mkdir()
    (more / "letter.txt").write_text("Dear Sissel, the meeting is on Monday.")
    library = c.post("/api/documents/sources", json={"folders": [str(more), " "], "urls": []}).json()
    assert library["folders"] == [str(more)] and skill.settings["folders"] == str(more)
    assert {d["name"] for d in library["documents"]} == {"letter.txt"}
    assert c.post("/api/documents/sources", json={"urls": ["file:///etc/passwd"]}).status_code == 400

"""The documents page's API: thin relays to the skill over the message bus,
and the upload, which writes into the skill's upload folder."""
import re
import unicodedata
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ovos_skill_documents.panel import MAX_UPLOAD

LIST = "ovos.documents.list"
RENAME = "ovos.documents.rename"
RESCAN = "ovos.documents.rescan"
SOURCES = "ovos.documents.sources"
#: scanning a folder of PDFs, or fetching a url, takes a while
TIMEOUT = 30.0


class RenameBody(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field("", max_length=300)


class SourcesBody(BaseModel):
    folders: list[str] = Field(default_factory=list, max_length=100)
    urls: list[str] = Field(default_factory=list, max_length=100)


class DeleteBody(BaseModel):
    id: str = Field(min_length=1, max_length=64)


def ask(r, msg_type: str, data: dict | None = None, timeout: float = TIMEOUT) -> dict[str, Any]:
    """Send a message to the skill and give back its answer."""
    from ovos_bus_client.message import Message
    bus = r.need_bus()
    message = Message(msg_type, data or {})
    try:
        from ovos_webui import buswait
        reply = buswait.wait_for_response(bus, message, timeout=timeout)
    except ImportError:  # an older panel: the bus client's own wait
        reply = bus.wait_for_response(message, timeout=timeout)
    if reply is None:
        raise HTTPException(504, "the documents skill did not answer; is it running?")
    return reply.data


def safe_name(name: str) -> str:
    """A file name that stays in the folder it is written to."""
    name = unicodedata.normalize("NFC", Path(name or "").name).strip()
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", "_", name).lstrip(".")
    return name[:180] or "document"


def free_path(folder: Path, name: str) -> Path:
    """``name`` in ``folder``, or ``name (2)`` and so on when it is taken."""
    path = folder / name
    stem, suffix = path.stem, path.suffix
    n = 2
    while path.exists():
        path = folder / f"{stem} ({n}){suffix}"
        n += 1
    return path


def upload_folder(library: dict[str, Any]) -> Path:
    folder = Path(library.get("upload_folder") or "")
    if not str(folder) or not folder.is_dir():
        # the skill runs somewhere the panel cannot write to
        raise HTTPException(409, "the skill's upload folder is not on this device")
    return folder


def inside(path: str, folder: Path) -> bool:
    try:
        Path(path).resolve().relative_to(folder.resolve())
    except (ValueError, OSError):
        return False
    return True


def store(library: dict[str, Any], name: str, blob: bytes) -> Path:
    """Write an uploaded document into the skill's upload folder."""
    name = safe_name(name)
    formats = {f.lower() for f in library.get("formats") or []}
    if Path(name).suffix.lower() not in formats:
        raise HTTPException(415, "that kind of file cannot be read; use one of " + ", ".join(sorted(formats)))
    if not blob:
        raise HTTPException(400, "the file is empty")
    if len(blob) > MAX_UPLOAD:
        raise HTTPException(413, f"a document can be at most {MAX_UPLOAD // (1024 * 1024)} MB")
    folder = upload_folder(library)
    path = free_path(folder, name)
    tmp = path.with_name(f".{path.name}.part")
    tmp.write_bytes(blob)
    tmp.replace(path)
    return path


def find(library: dict[str, Any], path: Path) -> dict[str, Any] | None:
    want = str(path.resolve())
    for doc in library.get("documents") or []:
        if doc.get("kind") == "file" and doc.get("source") and str(Path(doc["source"]).resolve()) == want:
            return doc
    return None


def routes(r) -> None:
    @r.api.get("/documents")
    def api_documents() -> dict[str, Any]:
        return ask(r, LIST, timeout=5.0)

    @r.api.post("/documents/rename")
    def api_documents_rename(body: RenameBody) -> dict[str, Any]:
        answer = ask(r, RENAME, {"id": body.id, "title": body.title.strip()}, timeout=5.0)
        if not answer.get("ok"):
            raise HTTPException(404, "there is no such document")
        return answer

    @r.api.post("/documents/rescan")
    def api_documents_rescan() -> dict[str, Any]:
        return ask(r, RESCAN)

    # Folders let the skill read any folder on the device, and urls make it
    # fetch from the internet, so they need the access token.
    @r.privileged.post("/documents/sources")
    def api_documents_sources(body: SourcesBody) -> dict[str, Any]:
        clean = {key: [v.strip() for v in values if v.strip()]
                 for key, values in (("folders", body.folders), ("urls", body.urls))}
        bad = [u for u in clean["urls"] if not re.match(r"^https?://\S+$", u)]
        if bad:
            raise HTTPException(400, "a url starts with http:// or https://: " + ", ".join(bad))
        return ask(r, SOURCES, clean)

    @r.privileged.post("/documents/upload")
    async def api_documents_upload(request: Request) -> dict[str, Any]:
        form = await request.form()
        upload = form.get("file")
        if not hasattr(upload, "read"):
            raise HTTPException(400, "no file was sent")
        blob = await upload.read()
        title = str(form.get("title") or "").strip()[:300]
        library = await run_in_threadpool(ask, r, LIST, None, 5.0)
        path = await run_in_threadpool(store, library, upload.filename or "", blob)
        library = await run_in_threadpool(ask, r, RESCAN)
        doc = find(library, path)
        if doc and title:
            await run_in_threadpool(ask, r, RENAME, {"id": doc["id"], "title": title}, 5.0)
            library = await run_in_threadpool(ask, r, LIST, None, 5.0)
            doc = find(library, path)
        return {"document": doc, "library": library}

    @r.privileged.post("/documents/delete")
    def api_documents_delete(body: DeleteBody) -> dict[str, Any]:
        library = ask(r, LIST, timeout=5.0)
        doc = next((d for d in library.get("documents") or [] if d.get("id") == body.id), None)
        if doc is None:
            raise HTTPException(404, "there is no such document")
        folder = upload_folder(library)
        # only what was uploaded here; a folder of your own is yours to tidy
        if doc.get("kind") != "file" or not inside(doc["source"], folder):
            raise HTTPException(403, "only uploaded documents can be deleted here")
        Path(doc["source"]).unlink(missing_ok=True)
        return ask(r, RESCAN)

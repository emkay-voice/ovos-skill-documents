"""The library: which documents there are, and what each is called.

Sources are folders on the device and urls. A folder is scanned with its
subfolders. A url is a document when it ends in a format this skill reads
(.pdf, .docx, ...); otherwise it is fetched as a page of links (a web
server's folder listing, or any page linking to documents), or as a plain
text list with one url per line, and every link to a document becomes one.

Every document has a stable id (from where it is), a title and, when the
document says, an author. The title is, in order: one the user gave it
(rename()), the document's own (PDF/Word/EPUB metadata, the first Markdown
heading, the HTML <title>), the link's text on a page of links, or the file
name. A local document is opened for its title only when it is new or has
changed since the last scan (the cache keeps its size and time).

Everything is kept in one JSON file the skill owns (library.json), so a
control-panel page can list the documents and rename them, and upload new
ones into the upload folder, without the skill changing.
"""
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import unquote, urljoin, urlparse

from .extract import DocumentError, is_supported, read_document, title_from_name


@dataclass
class Entry:
    id: str
    source: str          # a file path or a url
    kind: str            # "file" or "url"
    name: str            # the file name
    title: str           # what it is called (see the module docstring)
    author: str = ""
    custom_title: str = ""  # the user's own name for it, when given
    modified: float = 0.0   # file time, or when the url was first seen

    @property
    def host(self) -> str:
        """The web site a url's document is from; "" for a file."""
        if self.kind != "url":
            return ""
        return urlparse(self.source).hostname or ""


def document_id(source: str) -> str:
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]


def _norm_folder(folder: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(folder)))).resolve()


class Library:
    """Scans the sources into entries, keeping the user's titles and a
    cache of what each document says about itself in `path` (JSON)."""

    def __init__(self, path: Path, fetch: Callable[[str], tuple], log=None):
        """`fetch(url)` returns (bytes, content type) or raises."""
        self.path = Path(path)
        self.fetch = fetch
        self.log = log
        self.entries: Dict[str, Entry] = {}
        state = self._load()
        self.custom_titles: Dict[str, str] = state.get("titles", {})
        self.cache: Dict[str, dict] = state.get("cache", {})
        self.first_seen: Dict[str, float] = state.get("first_seen", {})

    # --- persistence ---------------------------------------------------------------------

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(self):
        state = {"titles": self.custom_titles, "cache": self.cache, "first_seen": self.first_seen,
                 "documents": [asdict(e) for e in self.sorted()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".part")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # --- the user's titles -----------------------------------------------------------------

    def rename(self, doc_id: str, title: Optional[str]) -> bool:
        """Give a document the user's own title; an empty one goes back to
        the automatic title. False when there is no such document."""
        entry = self.entries.get(doc_id)
        if entry is None:
            return False
        title = " ".join((title or "").split())
        if title:
            self.custom_titles[doc_id] = title
        else:
            self.custom_titles.pop(doc_id, None)
        cached = self.cache.get(doc_id, {})
        entry.custom_title = title
        entry.title = title or cached.get("title") or title_from_name(entry.name)
        self.save()
        return True

    # --- scanning --------------------------------------------------------------------------

    def scan(self, folders: Iterable[str], urls: Iterable[str]) -> bool:
        """Rebuild the entries from the sources; True when the set of
        documents or their titles changed."""
        before = {e.id: (e.title, e.author) for e in self.entries.values()}
        entries: Dict[str, Entry] = {}
        for folder in folders:
            for entry in self._scan_folder(folder):
                entries.setdefault(entry.id, entry)
        for url in urls:
            for entry in self._scan_url(url):
                entries.setdefault(entry.id, entry)
        for entry in entries.values():
            entry.custom_title = self.custom_titles.get(entry.id, "")
            if entry.custom_title:
                entry.title = entry.custom_title
        self.entries = entries
        # forget what is gone, so the file does not grow forever (the
        # user's titles stay: a folder that is offline now may come back)
        self.cache = {k: v for k, v in self.cache.items() if k in entries}
        self.first_seen = {k: v for k, v in self.first_seen.items() if k in entries}
        self.save()
        return before != {e.id: (e.title, e.author) for e in entries.values()}

    def _scan_folder(self, folder) -> List[Entry]:
        root = _norm_folder(folder)
        if not root.is_dir():
            return []
        found = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or not is_supported(path.name) or path.name.startswith((".", "~$")):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            source = str(path)
            doc_id = document_id(source)
            meta = self._file_meta(doc_id, path, stat)
            found.append(Entry(id=doc_id, source=source, kind="file", name=path.name,
                               title=meta.get("title") or title_from_name(path.name),
                               author=meta.get("author") or "", modified=stat.st_mtime))
        return found

    def _file_meta(self, doc_id: str, path: Path, stat) -> dict:
        """The document's own title and author, opened only when it is new
        or changed since the last scan."""
        cached = self.cache.get(doc_id)
        if cached and cached.get("size") == stat.st_size and cached.get("mtime") == stat.st_mtime:
            return cached
        meta = {"size": stat.st_size, "mtime": stat.st_mtime}
        try:
            doc = read_document(path.read_bytes(), path.name)
            meta.update(title=doc.title or "", author=doc.author or "")
        except (OSError, DocumentError) as e:
            self._warn(f"could not read {path}: {e}")
        self.cache[doc_id] = meta
        return meta

    def _scan_url(self, url: str) -> List[Entry]:
        url = url.strip()
        if not url:
            return []
        if is_supported(urlparse(url).path):
            return [self._url_entry(url, link_text="")]
        try:
            data, content_type = self.fetch(url)
        except Exception as e:
            self._warn(f"could not fetch {url}: {e}")
            return []
        text = data.decode("utf-8", errors="replace")
        if "html" in (content_type or "") or text.lstrip()[:1] == "<":
            links = self._links_in_page(text, url)
        else:  # a plain list of urls
            links = [(line.strip(), "") for line in text.splitlines()
                     if line.strip().startswith(("http://", "https://"))]
        return [self._url_entry(link, text) for link, text in links if is_supported(urlparse(link).path)]

    @staticmethod
    def _links_in_page(html: str, base: str) -> List[tuple]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        links, seen = [], set()
        for a in soup.find_all("a", href=True):
            link = urljoin(base, a["href"])
            if link in seen:
                continue
            seen.add(link)
            links.append((link, " ".join(a.get_text().split())))
        return links

    def _url_entry(self, url: str, link_text: str) -> Entry:
        doc_id = document_id(url)
        name = unquote(Path(urlparse(url).path).name)
        cached = self.cache.get(doc_id, {})
        # a folder listing's link text is just the file name again
        useful_text = link_text if link_text and link_text != name else ""
        title = cached.get("title") or useful_text or title_from_name(name)
        modified = self.first_seen.setdefault(doc_id, time.time())
        return Entry(id=doc_id, source=url, kind="url", name=name, title=title,
                     author=cached.get("author", ""), modified=modified)

    def learned(self, doc_id: str, title: Optional[str], author: Optional[str]) -> bool:
        """What a url's document said about itself when it was read: from
        then on that is its title (unless the user gave one). True when
        the entry changed."""
        entry = self.entries.get(doc_id)
        if entry is None or entry.kind != "url":
            return False
        cached = self.cache.setdefault(doc_id, {})
        changed = False
        if title and cached.get("title") != title:
            cached["title"] = title
            if not entry.custom_title:
                entry.title, changed = title, True
        if author and cached.get("author") != author:
            cached["author"] = entry.author = author
            changed = True
        if changed:
            self.save()
        return changed

    # --- reading -----------------------------------------------------------------------------

    def sorted(self) -> List[Entry]:
        """Newest first."""
        return sorted(self.entries.values(), key=lambda e: (-e.modified, e.title.lower()))

    def newest(self) -> Optional[Entry]:
        entries = self.sorted()
        return entries[0] if entries else None

    def read(self, doc_id: str):
        """The document's text (extract.Document), from disk or its url."""
        entry = self.entries.get(doc_id)
        if entry is None:
            raise DocumentError(f"no document {doc_id}")
        if entry.kind == "file":
            data = Path(entry.source).read_bytes()
        else:
            data, _ = self.fetch(entry.source)
        doc = read_document(data, entry.name)
        if entry.kind == "url":
            self.learned(doc_id, doc.title, doc.author)
        return doc

    def _warn(self, text):
        if self.log:
            self.log.warning(text)

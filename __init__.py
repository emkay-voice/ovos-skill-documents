"""
skill OVOS Documents

A provider for ovos-common-reading-pipeline-plugin that reads your own
documents aloud: "read me the document <title>", "read my documents",
"læs dokumentet <titel>". Text files, Markdown, PDF, Word (.docx),
OpenDocument (.odt), EPUB and HTML, from folders on the device and from urls
(a document's own url, or a page linking to documents such as a web server's
folder listing). See library.py for how the documents are found and named,
and extract.py for how their text is taken out.

Made for Emkay (https://github.com/emkay-voice/emkay), and meant for any
OpenVoiceOS device. It is ready for a control-panel page that uploads
documents and names them: the upload folder is scanned like any other, and
the ovos.documents.* messages below list, rename and rescan the library.

DECISIONS (see ovos-skill-common-reading-example for the list every provider
goes through):

- No translation. A document is read in the language it is asked for, as
  written; machine-translating someone's own papers is not something to do
  behind their back.
- It answers in every language the installation is configured for: the
  documents are the user's, in whatever language they are.
- Vocabulary: "document"/"file" and "my documents" in the 8 languages of the
  reading family, and the titles of at least two words ("tell me <title>"):
  a one-word title is too likely an everyday word. Never "report" or "notes",
  which belong to the weather and notes skills.
- A request that names no title ("read me a document") gets the newest one.
- Nothing is fetched from the internet unless a url is configured.
"""
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from ovos_bus_client.message import Message
from ovos_bus_client.session import SessionManager
from ovos_utils import classproperty
from ovos_utils.parse import match_one
from ovos_utils.process_utils import RuntimeRequirements
from ovos_workshop.skills import OVOSSkill

from .extract import SUPPORTED, DocumentError
from .library import Library

COMMON_READING_SEARCH = "ovos.common_reading.search"
COMMON_READING_SEARCH_RESPONSE = "ovos.common_reading.search.response"
COMMON_READING_FETCH_CONTENT = "ovos.common_reading.fetch_content"  # + ".{skill_id}"
COMMON_READING_FETCH_CONTENT_RESPONSE = "ovos.common_reading.fetch_content.response"
COMMON_READING_PING = "ovos.common_reading.ping"
COMMON_READING_PONG = "ovos.common_reading.pong"
COMMON_READING_VOCABULARY = "ovos.common_reading.vocabulary"
COMMON_READING_VOCABULARY_GET = "ovos.common_reading.vocabulary.get"

# for a control-panel page (or anything else): the library, as data
DOCUMENTS_LIST = "ovos.documents.list"        # -> .response {"documents": [...], ...}
DOCUMENTS_RENAME = "ovos.documents.rename"    # {"id", "title"} ("" = automatic) -> .response {"ok"}
DOCUMENTS_RESCAN = "ovos.documents.rescan"    # -> .response, as list
DOCUMENTS_SOURCES = "ovos.documents.sources"  # {"folders", "urls"} -> .response, as list

CONTENT_TYPES = ["document"]
NO_TITLE_CONFIDENCE = 0.9
COLLECTION_HINT_THRESHOLD = 0.85
MIN_TITLE_SCORE = 0.5  # below this a title is not the one asked for
RESCAN_MINUTES = 10
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
FALLBACK_COLLECTION_ALIASES = ["my documents", "my files", "the documents"]
FALLBACK_CONTENT_WORDS = ["document", "documents", "the document", "file", "the file", "pdf"]


def _user_agent():
    try:
        from importlib.metadata import version
        ver = version("ovos-skill-documents")
    except Exception:
        ver = "unknown"
    return f"ovos-skill-documents/{ver} (+https://github.com/emkay-voice/ovos-skill-documents)"


HTTP_HEADERS = {"User-Agent": _user_agent()}


def primary_subtag(lang):
    return (lang or "").replace("_", "-").split("-")[0].lower()


def configured_languages(langs):
    return {primary_subtag(lang) for lang in langs or [] if lang}


def _read_voc(path):
    """Phrases in a .voc file: one per line, "a|b" and "(a|b) c" expanded."""
    phrases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(.*)\(([^)]*)\)(.*)$", line)
        if m:
            phrases += [" ".join(f"{m.group(1)}{alt}{m.group(3)}".split()) for alt in m.group(2).split("|")]
        else:
            phrases += [alt.strip() for alt in line.split("|") if alt.strip()]
    return phrases


def _as_list(value):
    """A setting given as a list, or as text with one per line or comma."""
    if not value:
        return []
    if isinstance(value, str):
        value = re.split(r"[\n,]", value)
    return [str(v).strip() for v in value if str(v).strip()]


class DocumentsSkill(OVOSSkill):

    @classproperty
    def runtime_requirements(self):
        # folders need nothing; a configured url is fetched when it can be
        return RuntimeRequirements(internet_before_load=False,
                                   network_before_load=False,
                                   requires_internet=False,
                                   requires_network=False,
                                   no_internet_fallback=True,
                                   no_network_fallback=True)

    def initialize(self):
        base = Path(self.file_system.path)
        self.upload_folder = base / "uploads"
        self.upload_folder.mkdir(parents=True, exist_ok=True)
        self.text_cache = base / "text"
        self._lock = threading.RLock()
        self._preparing = {}  # document id -> threading.Event, while its text is read
        self.library = Library(base / "library.json", fetch=self._fetch, log=self.log)
        self.add_event(COMMON_READING_SEARCH, self.handle_search)
        self.add_event(f"{COMMON_READING_FETCH_CONTENT}.{self.skill_id}", self.handle_fetch_content)
        self.add_event(COMMON_READING_PING, self.handle_ping)
        self.add_event(COMMON_READING_VOCABULARY_GET, self.handle_vocabulary_get)
        self.add_event(DOCUMENTS_LIST, self.handle_list)
        self.add_event(DOCUMENTS_RENAME, self.handle_rename)
        self.add_event(DOCUMENTS_RESCAN, self.handle_rescan)
        self.add_event(DOCUMENTS_SOURCES, self.handle_sources)
        self.settings_change_callback = self.rescan
        self.rescan(announce=False)
        self._announce_vocabulary()
        minutes = float(self.settings.get("rescan_minutes") or RESCAN_MINUTES)
        if minutes > 0:
            self.schedule_repeating_event(self.rescan, None, minutes * 60, name="rescan")

    # --- sources -------------------------------------------------------------------------

    def folders(self):
        """The configured folders, and the upload folder."""
        return _as_list(self.settings.get("folders")) + [str(self.upload_folder)]

    def urls(self):
        return _as_list(self.settings.get("urls"))

    def _fetch(self, url):
        import requests
        r = requests.get(url, timeout=20, headers=HTTP_HEADERS, stream=True)
        r.raise_for_status()
        data = r.raw.read(MAX_DOCUMENT_BYTES + 1, decode_content=True)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise DocumentError(f"{url} is larger than {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB")
        return data, r.headers.get("content-type", "")

    def rescan(self, message=None, announce=True):
        with self._lock:
            changed = self.library.scan(self.folders(), self.urls())
        if changed and announce:
            self._announce_vocabulary()
        return changed

    # --- languages and words -------------------------------------------------------------

    @staticmethod
    def _request_lang(message):
        lang = message.data.get("lang") or message.context.get("lang")
        if not lang and message.context.get("session"):
            lang = SessionManager.get(message).lang
        return lang or None

    def _serves(self, lang):
        return primary_subtag(lang) in configured_languages(self.native_langs)

    def _locale_words(self, name, lang):
        base = Path(__file__).resolve().parent / "locale"
        for folder in sorted(base.iterdir()) if base.is_dir() else []:
            if primary_subtag(folder.name) == primary_subtag(lang) and (folder / f"{name}.voc").is_file():
                return _read_voc(folder / f"{name}.voc")
        return []

    def _aliases(self, lang):
        return self._locale_words("collection", lang) or FALLBACK_COLLECTION_ALIASES

    def _vocabulary(self, lang):
        titles = [e.title for e in self.library.entries.values() if len(e.title.split()) >= 2]
        return {"content_types": {CONTENT_TYPES[0]: self._locale_words("content_type", lang)
                                  or FALLBACK_CONTENT_WORDS},
                "collections": self._aliases(lang),
                "titles": sorted(set(titles))}

    def _announce_vocabulary(self, langs=None, message=None):
        wanted = {primary_subtag(str(l)) for l in (langs or [])}
        for lang in sorted(configured_languages(self.native_langs)):
            if wanted and lang not in wanted:
                continue
            data = {"skill_id": self.skill_id, "lang": lang, **self._vocabulary(lang)}
            self.bus.emit(message.reply(COMMON_READING_VOCABULARY, data) if message
                          else Message(COMMON_READING_VOCABULARY, data))

    def handle_vocabulary_get(self, message):
        self._announce_vocabulary(message.data.get("langs"), message)

    # --- the reading protocol --------------------------------------------------------------

    def handle_search(self, message):
        lang = self._request_lang(message) or self.lang
        if not self._serves(lang) or not self.library.entries:
            return
        hint = message.data.get("collection_hint")
        if hint:
            _, score = match_one(hint.lower(), [a.lower() for a in self._aliases(lang)])
            if score < COLLECTION_HINT_THRESHOLD:
                return
        content_type = message.data.get("content_type")
        if content_type and content_type.lower() not in CONTENT_TYPES:
            return
        phrase = (message.data.get("phrase") or "").strip()
        if phrase:
            by_title = {}
            for entry in self.library.sorted():  # the newest wins a shared title
                by_title.setdefault(entry.title.lower(), entry)
            match, confidence = match_one(phrase.lower(), list(by_title))
            if confidence < MIN_TITLE_SCORE:
                return
            entry = by_title[match]
        else:
            entry = self.library.newest()
            confidence = 1.0 if hint else NO_TITLE_CONFIDENCE
        self.bus.emit(message.reply(COMMON_READING_SEARCH_RESPONSE, {
            "skill_id": self.skill_id,
            "content_id": entry.id,
            "title": entry.title,
            "author": entry.author,
            "source": entry.host or None,
            "confidence": confidence,
            "machine_translated": False,
        }))
        # most answers are read next: start on the text while the pipeline
        # announces it, a long PDF can take a while
        self._prepare(entry.id)

    def handle_fetch_content(self, message):
        doc_id = message.data.get("content_id")
        paragraphs = self._paragraphs(doc_id) if doc_id else []
        self.bus.emit(message.reply(COMMON_READING_FETCH_CONTENT_RESPONSE,
                                    {"paragraphs": paragraphs or []}))

    def handle_ping(self, message):
        lang = self._request_lang(message)
        if lang and not self._serves(lang):
            return
        self.bus.emit(message.reply(COMMON_READING_PONG, {"skill_id": self.skill_id}))

    # --- text, read once ---------------------------------------------------------------------

    def _cache_file(self, doc_id):
        entry = self.library.entries.get(doc_id)
        if entry is None:
            return None
        stamp = f"{entry.source}\n{entry.modified}\n{self.library.cache.get(doc_id, {}).get('size', '')}"
        if entry.kind == "url":  # it can change where it is: fetched again each day
            stamp += "\n" + time.strftime("%Y-%m-%d")
        return self.text_cache / f"{doc_id}-{hashlib.sha1(stamp.encode()).hexdigest()[:8]}.json"

    def _prepare(self, doc_id):
        """Read the document's text on a background thread, unless it is
        cached or already being read."""
        cache = self._cache_file(doc_id)
        if cache is None or cache.exists():
            return
        with self._lock:
            if doc_id in self._preparing:
                return
            self._preparing[doc_id] = threading.Event()
        threading.Thread(target=self._read_text, args=(doc_id,), daemon=True,
                         name=f"ovos-skill-documents-{doc_id}").start()

    def _read_text(self, doc_id):
        try:
            doc = self.library.read(doc_id)
            cache = self._cache_file(doc_id)  # after read(): a url may have told its title
            cache.parent.mkdir(parents=True, exist_ok=True)
            for old in cache.parent.glob(f"{doc_id}-*.json"):
                old.unlink()
            cache.write_text(json.dumps(doc.paragraphs, ensure_ascii=False), encoding="utf-8")
        except (OSError, DocumentError, ValueError) as e:
            self.log.error(f"could not read document {doc_id}: {e}")
        except Exception as e:  # a url that cannot be fetched
            self.log.error(f"could not get document {doc_id}: {e}")
        finally:
            with self._lock:
                event = self._preparing.pop(doc_id, None)
            if event:
                event.set()

    def _paragraphs(self, doc_id):
        if doc_id not in self.library.entries:
            return []
        self._prepare(doc_id)
        with self._lock:
            event = self._preparing.get(doc_id)
        if event:
            event.wait(9)  # inside the pipeline's 10 s
        for cached in sorted(self.text_cache.glob(f"{doc_id}-*.json")) if self.text_cache.is_dir() else []:
            try:
                return json.loads(cached.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return []
        return []

    # --- the library, as data (for a control-panel page) ------------------------------------

    def _library_data(self):
        return {"documents": [{"id": e.id, "title": e.title, "author": e.author, "name": e.name,
                               "kind": e.kind, "source": e.source, "custom_title": e.custom_title,
                               "modified": e.modified} for e in self.library.sorted()],
                "upload_folder": str(self.upload_folder),
                "folders": _as_list(self.settings.get("folders")),
                "urls": self.urls(),
                "formats": sorted(SUPPORTED)}

    def handle_list(self, message):
        self.bus.emit(message.response(self._library_data()))

    def handle_rename(self, message):
        with self._lock:
            ok = self.library.rename(message.data.get("id"), message.data.get("title"))
        if ok:
            self._announce_vocabulary()
        self.bus.emit(message.response({"ok": ok}))

    def handle_rescan(self, message):
        self.rescan()
        self.bus.emit(message.response(self._library_data()))

    def handle_sources(self, message):
        """Set the folders and urls (a list, or text with one per line), save
        them in the skill's settings, and look for documents at once."""
        for key in ("folders", "urls"):
            if key in message.data:
                self.settings[key] = "\n".join(_as_list(message.data.get(key)))
        store = getattr(self.settings, "store", None)
        if callable(store):
            store()
        self.rescan()
        self.bus.emit(message.response(self._library_data()))

    def shutdown(self):
        try:
            self.bus.emit(Message(COMMON_READING_VOCABULARY, {"skill_id": self.skill_id, "remove": True}))
        except Exception:
            pass
        super().shutdown()

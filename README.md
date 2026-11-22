# <img src='icon.png' card_color='#3A86C8' width='50' height='50' style='vertical-align:bottom'/> Documents

Reads your own documents aloud: text files, Markdown, PDF, Word (.docx), OpenDocument (.odt), EPUB and HTML, from
folders on the device and from urls.

It is a provider for the [common reading pipeline](https://github.com/andlo/ovos-common-reading-pipeline-plugin),
like the fairy-tale and article providers, so a document reads the way a story does: announced, read a sentence at a
time, paused, continued where it stopped, and (with the pipeline's `"player": "ovos-media"`) read through the media
player with its controls.

Made for [Emkay](https://github.com/emkay-voice/emkay), and meant for any OpenVoiceOS device.

## Usage

- "Read me the document holiday plans"
- "Read my documents" / "Read me a document" (the newest one)
- "Tell me board minutes march" (a title of two words or more on its own)
- "Læs dokumentet ferieplaner", "Læs mine dokumenter"
- and "pause", "continue", "stop" as for any reading

It understands "document", "file" and "my documents" in English, Danish, German, Spanish, French, Italian, Dutch and
Portuguese, and answers in every language the device is set up for. A document is read as it is written; nothing is
translated.

## Where it finds documents

In the skill's settings, one per line:

- **Folders** on the device, read with their subfolders (`~/Documents/Reading`, `/media/usb/books`, ...).
- **Urls**: a url to a document (`https://example.com/letter.pdf`) is that document. Any other url is fetched as a
  page of links - a web server's folder listing, or any page that links to documents - or as a plain text list with
  one url per line, and every link to a document becomes one.

The skill's own **upload folder** (`uploads` in its data folder) is always read too. It looks for new documents every
10 minutes (`rescan_minutes`, 0 = only at start).

Nothing is fetched from the internet unless you add a url. A url's document is fetched when it is read (at most once
a day; its text is kept), not when the skill looks for documents.

## What a document is called

In this order: the name you gave it, the document's own title (PDF/Word/OpenDocument/EPUB metadata, the first
Markdown heading, the HTML `<title>`), the link's text on a page of links, or the file name (`min_rapport-2024.pdf` is
"min rapport 2024"). The author is read from the document when it has one.

## What is read

Paragraphs, headings and list items, in order. A table row is read as one line with its cells joined by ": ", so a
play reads "Ivanov: I am reading". Left out because they don't read well aloud: code blocks, page numbers, footnote
markers, navigation, scripts and styles, and an EPUB's own edition pages (title page, imprint, colophon, tables of
contents, notes). A word hyphenated at the end of a line is joined again. A
PDF seldom marks its paragraphs, so it often reads as one paragraph per page; the pipeline reads it sentence by
sentence all the same. Scanned PDFs (images of text) have no text to read.

## In the control panel

With [ovos-control-panel](https://github.com/OpenVoiceOS/ovos-control-panel) on the device, the skill brings
its own page, **Documents**, under **Library**. There you can:

- upload a document (up to 50 MB) and give it a title to ask for it by,
- give any document another title, or go back to its own,
- delete documents you uploaded (your own folders are left alone),
- set the folders and web addresses it looks in.

Setting folders and addresses, uploading and deleting need the panel's access token. The page is described in
`controlpanel.json`, with its words in `locale/<lang>/controlpanel.json`: the panel draws it, and no code of the
skill runs in the panel.

### The messages behind it

| Message | Data | Answer (`<message>.response`) |
|---|---|---|
| `ovos.documents.list` | | `documents` (id, title, author, name, kind, source, custom_title, modified, uploaded; newest first), `upload_folder`, `folders`, `urls`, `formats` |
| `ovos.documents.rename` | `id`, `title` (`""` = back to the automatic title) | `ok` |
| `ovos.documents.rescan` | | as `list` |
| `ovos.documents.sources` | `folders`, `urls` (lists, or text with one per line) | as `list`, after saving them in the settings and looking again, or `error` |
| `ovos.documents.upload` | a file in parts: `upload` (one id), `name`, `part` (from 0), `parts`, `data` (base64), `title` | `ok` for a part; for the last, as `list` with `document`; or `error` |
| `ovos.documents.delete` | `id` of an uploaded document | as `list`, or `error` |

An upload comes over the message bus, so the panel can run on another device than the skill; the skill writes it
in `upload_folder`. The library is also kept in `library.json` in the skill's data folder.

## Install

```bash
pip install ovos-skill-documents
```

It needs [ovos-common-reading-pipeline-plugin](https://github.com/andlo/ovos-common-reading-pipeline-plugin) in the
intent pipeline, as every reading provider does.

## Development

```bash
pip install -e ".[test]"
pytest tests
```

The tests build their own sample documents of every format (nothing binary is committed), and `test_with_the_pipeline.py`
checks with the real reading pipeline that the requests above are understood and that "the weather report" and the
like are left to their own skills.

## Category
**Entertainment**, **Productivity**

## Tags
#documents #reading #pdf #word #epub #commonreading #provider

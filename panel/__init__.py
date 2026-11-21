"""The documents page in ovos-control-panel.

The control panel finds this page through the ``opm.control_panel.page``
entry point (see pyproject.toml and the panel's docs/pages.md). It lists the
documents the skill has found, lets you upload your own and give them a
title, and sets the folders and urls the skill looks in. Nothing here is
loaded by the skill itself, so the skill needs no web packages; the panel
brings those.
"""

#: the largest document the page takes, the same as the skill reads from a url
MAX_UPLOAD = 50 * 1024 * 1024


def routes(r):
    # Imported here, so reading PAGE (the panel's registry, this skill's
    # tests) needs no web framework; only the panel calls this.
    from ovos_skill_documents.panel.api import routes as add
    add(r)


#: the group the reading skills share; any of them may bring it, the first wins
READING = {"id": "reading", "title": {"key": "reading.group", "label": "Reading"},
           "order": 5, "level": "everyday"}

PAGE = {
    "api_version": 1,
    "id": "documents",
    "path": "/documents",
    "title": {"key": "documents.title", "label": "Documents"},
    "group": "reading",
    "groups": [READING],
    "order": 10,
    "needs": ["ovos-skill-documents"],
    "html": "page.html",
    "icon": "icon.svg",
    "i18n": "i18n",
    "routes": routes,
    "uploads": {"/api/documents/upload": MAX_UPLOAD + 64 * 1024},
}

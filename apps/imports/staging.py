"""Holding an uploaded file between wizard steps — TODO 1.10.1.

The wizard is upload → map → preview → commit, and every step after the first
needs the same bytes again. A browser cannot re-populate a file input, so asking
for the file once per step would mean choosing it three times.

⚠ The staged file is a **whole roster**: names, birthdates, parents' addresses.
It is therefore keyed by a name this module generates, never by anything the
operator sends; tied to the session that uploaded it and to that session's
organisation; and deleted as soon as the import commits. It also lives under
``MEDIA_ROOT``, which is only safe because nothing serves that directory to the
web — see the Caddyfile, which explains at length why it must stay that way.

Staging is not a document store: nothing here is a ``Document``, nothing is
audited as one, and none of it survives the wizard.
"""

from __future__ import annotations

import datetime
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

SESSION_KEY = "import_staging"
PREFIX = "import-staging"

#: A stale upload is discarded rather than reused. Long enough to read a preview
#: and think about it, short enough that an abandoned roster does not sit on disk.
MAX_AGE = datetime.timedelta(hours=2)


def _path(token: str) -> str:
    return f"{PREFIX}/{token}.csv"


def save(request, raw: bytes, *, filename: str, organization_id) -> str:
    """Stage bytes for this session, replacing anything it staged before."""
    discard(request)
    token = uuid.uuid4().hex
    default_storage.save(_path(token), ContentFile(raw))
    request.session[SESSION_KEY] = {
        "token": token,
        "filename": filename[:255],
        "organization_id": str(organization_id),
        "staged_at": timezone.now().isoformat(),
    }
    return token


def load(request, *, organization_id) -> tuple[bytes, str] | None:
    """Return ``(raw, filename)`` for this session's staged file, or None.

    ⚠ The organisation is re-checked rather than trusted. A session that changed
    tenant between steps — impossible today, but a login as somebody else in the
    same browser is not — must not carry a roster across.
    """
    meta = request.session.get(SESSION_KEY)
    if not meta:
        return None
    if meta.get("organization_id") != str(organization_id):
        discard(request)
        return None
    try:
        staged_at = datetime.datetime.fromisoformat(meta["staged_at"])
    except (KeyError, ValueError):
        discard(request)
        return None
    if timezone.now() - staged_at > MAX_AGE:
        discard(request)
        return None

    path = _path(meta["token"])
    if not default_storage.exists(path):
        discard(request)
        return None
    with default_storage.open(path, "rb") as handle:
        return handle.read(), meta.get("filename", "import.csv")


def discard(request) -> None:
    """Delete the staged file and forget it. Safe to call when there is none."""
    meta = request.session.pop(SESSION_KEY, None)
    if not meta:
        return
    path = _path(meta.get("token", ""))
    if meta.get("token") and default_storage.exists(path):
        default_storage.delete(path)

"""Storing and serving documents — TODO 0.3.9b / SEC 2.3, 2.6.

Two operations, both deliberately narrow:

``store()``   validate, strip metadata, write outside the web root, record it.
``open_document()``  authorise, audit, then hand back bytes.

There is no third path. In particular there is no URL that maps to a file on
disk: every read of a minor's medical letter passes an access check and leaves
an audit entry, which is the difference between answering "who looked at this
child's record?" and shrugging.
"""

from __future__ import annotations

import hashlib

from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage

from .audit import record
from .models import AuditLog, Document
from .scoping import Actor
from .uploads import strip_image_metadata, validate_upload

#: Document kinds that always count as sensitive regardless of what the caller
#: says, because misclassifying these is the expensive direction of the error.
ALWAYS_SENSITIVE = {Document.Kind.MEDICAL, Document.Kind.IDENTITY}


def store(
    uploaded_file,
    *,
    organization,
    kind: str,
    actor: Actor,
    subject_person=None,
    retention_until=None,
) -> Document:
    """Validate and store an upload, returning the Document record."""
    file_kind = validate_upload(uploaded_file)

    cleaned = strip_image_metadata(uploaded_file, file_kind)
    if cleaned is not None:
        payload = cleaned.getvalue()
    else:
        uploaded_file.seek(0)
        payload = uploaded_file.read()

    document = Document(
        organization=organization,
        subject_person=subject_person,
        uploaded_by_id=actor.person_id if actor else None,
        kind=kind,
        original_filename=(getattr(uploaded_file, "name", "") or "")[:255],
        content_type=file_kind.mime,
        byte_size=len(payload),
        checksum=hashlib.sha256(payload).hexdigest(),
        is_sensitive=kind in ALWAYS_SENSITIVE,
        retention_until=retention_until,
    )

    from .uploads import generated_storage_name

    document.storage_key = generated_storage_name(document.pk, file_kind)
    document.save()

    from django.core.files.base import ContentFile

    default_storage.save(document.storage_key, ContentFile(payload))

    record(
        AuditLog.Action.CREATE,
        actor=actor,
        subject=document,
        organization_id=organization.pk,
        note=f"uploaded {kind}",
    )
    return document


def may_read(actor: Actor, document: Document, *, governance_model: str) -> bool:
    """Whether this actor may read this document.

    Deliberately conservative: sensitive documents require the medical-view
    permission, everything else requires being able to view the subject.
    """
    from apps.identity.models import StudentProfile
    from apps.identity.permissions import ROLE_ACTIONS, Action, can

    if actor is None or actor.is_anonymous:
        return False
    if document.organization_id != actor.organization_id:
        return False
    if document.kind == Document.Kind.PHOTO:
        from apps.identity.models import (
            ConsentPolicy,
            ConsentRecord,
            GovernanceModel,
        )
        from apps.identity.visibility import is_org_scoped_only

        if governance_model == GovernanceModel.FEDERATED and is_org_scoped_only(actor):
            return False
        if document.subject_person_id is None:
            return False
        policy = (
            ConsentPolicy.objects.for_organization(document.organization_id)
            .filter(consent_type=ConsentRecord.Type.PHOTO, is_active=True)
            .first()
        )
        if policy is None:
            return False
        decision = (
            ConsentRecord.objects.for_organization(document.organization_id)
            .filter(
                person_id=document.subject_person_id,
                consent_type=ConsentRecord.Type.PHOTO,
                version=policy.version,
            )
            .order_by("-granted_at", "-created_at")
            .first()
        )
        if decision is None or not decision.granted:
            return False

    subject = document.subject_person
    required = Action.MEDICAL_VIEW if document.is_sensitive else Action.PERSON_VIEW
    if subject is None:
        # Organisation-authored documents such as the current waiver have no
        # student subject. A same-tenant scoped role holding the permission may
        # read them; the document contains no individual PII.
        return any(required in ROLE_ACTIONS.get(role, set()) for role, _scope, _dojo in actor.roles)
    try:
        target = subject.student_profile
    except StudentProfile.DoesNotExist:
        target = subject
    return can(actor, required, target, governance_model=governance_model)


def open_document(actor: Actor, document: Document, *, governance_model: str) -> bytes:
    """Authorise, audit, and return the file's bytes.

    Raises ``PermissionDenied``. The denial is audited too — repeated refusals
    against documents belonging to one child is exactly the pattern SEC §2.6
    wants alerting on.
    """
    permitted = may_read(actor, document, governance_model=governance_model)

    record(
        AuditLog.Action.VIEW,
        actor=actor,
        subject=document,
        organization_id=document.organization_id,
        note="document read" if permitted else "document read DENIED",
    )

    if not permitted:
        raise PermissionDenied("You may not read this document.")

    with default_storage.open(document.storage_key, "rb") as handle:
        return handle.read()


def download_headers(document: Document) -> dict[str, str]:
    """Response headers for serving a document.

    ``attachment`` rather than ``inline``: a PDF rendered in-page runs its own
    JavaScript in our origin. The sandbox CSP is belt and braces on top.
    """
    return {
        "Content-Type": document.content_type,
        "Content-Disposition": f'attachment; filename="{_safe_filename(document)}"',
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
    }


def _safe_filename(document: Document) -> str:
    """Strip anything that could break out of the header or the filesystem."""
    name = document.original_filename or "document"
    cleaned = "".join(c for c in name if c.isalnum() or c in "._- ")
    return cleaned.strip() or "document"

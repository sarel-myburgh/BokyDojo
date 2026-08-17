"""The import wizard — TODO 1.10.1, 1.10.7, plan §12.10.

One screen, four states: choose a file, map its columns, read the dry run,
commit. The engine underneath is the same one ``manage.py import_csv`` drives, so
the wizard cannot drift from the command or from what a dry run promised.

⚠ **Commit is never the default and never reachable in one step.** The preview is
produced by actually running the import and rolling it back, so pressing Commit
runs the identical code against the identical file. Anything that let an operator
skip the preview would make that guarantee worthless.
"""

from __future__ import annotations

import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.core.reports import csv_report_response
from apps.identity.models import Dojo
from apps.identity.permissions import ROLE_ACTIONS, Action, PermissionDenied

from . import csv_source, engine, guessing, staging
from .models import ImportRun
from .students import StudentImporter, require_import_permission

IMPORTERS = {"students": StudentImporter}

#: How many of the operator's own rows to show beside the mapping controls.
#: Enough to recognise a mis-mapped column, few enough to stay on one screen.
PREVIEW_ROWS = 5


def _holds_anywhere(actor, action: str) -> bool:
    return any(action in ROLE_ACTIONS.get(role, set()) for role, _scope, _dojo in actor.roles)


def _importable_dojos(actor):
    """Dojos this actor may actually import into.

    ⚠ Filtered by the same permission the import itself requires, not merely by
    scope. A front-desk user can see a dojo they cannot bulk-create people in,
    and offering it would produce a refusal after they had chosen a file.
    """
    allowed = []
    for dojo in Dojo.objects.for_actor(actor).select_related("organization").order_by("name"):
        try:
            require_import_permission(actor, dojo)
        except PermissionDenied:
            continue
        allowed.append(dojo)
    return allowed


def _resolve_dojo(actor, raw):
    if not raw:
        return None
    try:
        dojo_id = uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        raise Http404("malformed dojo id") from None
    dojo = Dojo.objects.for_actor(actor).select_related("organization").filter(pk=dojo_id).first()
    if dojo is None:
        raise Http404("no such dojo")
    require_import_permission(actor, dojo)
    return dojo


def _columns(headers: list[str], rows: list[dict], mapping: dict[str, str]) -> list[dict]:
    """One entry per source column, ready to render.

    Built here rather than in the template so the mapping table needs no custom
    filter to look a header up in a row dict — Django templates cannot subscript
    by a variable key, and a filter for it would be a worse answer than shaping
    the data properly.
    """
    return [
        {
            "header": header,
            "guess": mapping.get(header, ""),
            "samples": [row.values.get(header, "") for row in rows[:PREVIEW_ROWS]],
        }
        for header in headers
    ]


def _mapping_from_post(request, headers: list[str]) -> dict[str, str]:
    """Read the select for each header. Unmapped columns are simply absent."""
    mapping: dict[str, str] = {}
    for header in headers:
        chosen = (request.POST.get(f"map:{header}") or "").strip()
        if chosen:
            mapping[header] = chosen
    return mapping


@login_required
@require_http_methods(["GET", "POST"])
def import_wizard_view(request) -> HttpResponse:
    """Upload → map → preview → commit — TODO 1.10.1."""
    actor = request.actor
    if not _holds_anywhere(actor, Action.PERSON_CREATE):
        raise PermissionDenied(action=Action.PERSON_CREATE, actor=actor)

    dojos = _importable_dojos(actor)
    importer = StudentImporter()
    context = {
        "dojos": dojos,
        "fields": sorted(importer.fields),
        "required_fields": sorted(field for field, req in importer.fields.items() if req),
        "preview_rows": PREVIEW_ROWS,
    }

    if request.method == "GET":
        staging.discard(request)
        return render(request, "imports/wizard.html", context)

    action = request.POST.get("action")

    # -- step 1: a file arrives ---------------------------------------------
    if action == "upload":
        dojo = _resolve_dojo(actor, request.POST.get("dojo"))
        if dojo is None:
            messages.error(request, _("Choose which dojo these students belong to."))
            return render(request, "imports/wizard.html", context)

        uploaded = request.FILES.get("file")
        if uploaded is None:
            messages.error(request, _("Choose a CSV file to import."))
            return render(request, "imports/wizard.html", context)
        if uploaded.size > csv_source.MAX_CSV_BYTES:
            messages.error(request, _("That file is too large to import."))
            return render(request, "imports/wizard.html", context)

        raw = uploaded.read()
        try:
            headers, rows = csv_source.read_table(raw)
        except ValidationError as exc:
            messages.error(request, " ".join(str(m) for m in exc.messages))
            return render(request, "imports/wizard.html", context)

        staging.save(
            request,
            raw,
            filename=uploaded.name,
            organization_id=dojo.organization_id,
        )
        guessed = guessing.guess(headers, importer.fields)
        context.update(
            step="map",
            dojo=dojo,
            headers=headers,
            columns=_columns(headers, rows, guessed),
            row_count=len(rows),
            mapping=guessed,
            filename=uploaded.name,
        )
        return render(request, "imports/wizard.html", context)

    # -- steps 2 and 3: preview, then commit --------------------------------
    if action in ("preview", "commit"):
        dojo = _resolve_dojo(actor, request.POST.get("dojo"))
        if dojo is None:
            raise Http404("no dojo")

        staged = staging.load(request, organization_id=dojo.organization_id)
        if staged is None:
            messages.error(
                request,
                _("That upload has expired. Choose the file again."),
            )
            return render(request, "imports/wizard.html", context)

        raw, filename = staged
        headers, rows = csv_source.read_table(raw)
        mapping = _mapping_from_post(request, headers)

        try:
            engine.validate_mapping(importer, mapping)
        except ValidationError as exc:
            messages.error(request, " ".join(str(m) for m in exc.messages))
            context.update(
                step="map",
                dojo=dojo,
                headers=headers,
                columns=_columns(headers, rows, mapping),
                row_count=len(rows),
                mapping=mapping,
                filename=filename,
            )
            return render(request, "imports/wizard.html", context)

        import_run = engine.run(
            importer=importer,
            rows=rows,
            mapping=mapping,
            actor=actor,
            dojo=dojo,
            filename=filename,
            dry_run=action == "preview",
        )

        if action == "commit":
            # The bytes have done their job. Holding a roster on disk after the
            # import is finished buys nothing and risks something.
            staging.discard(request)
            messages.success(
                request,
                _("Imported %(created)s new and updated %(updated)s existing student(s).")
                % {
                    "created": import_run.created_count,
                    "updated": import_run.updated_count,
                },
            )

        context.update(
            step="result",
            dojo=dojo,
            headers=headers,
            mapping=mapping,
            filename=filename,
            run=import_run,
            outcomes=import_run.outcomes[:200],
            outcomes_truncated=max(0, len(import_run.outcomes) - 200),
        )
        return render(request, "imports/wizard.html", context)

    raise Http404("unknown step")


@login_required
@require_http_methods(["GET"])
def import_report_view(request, run_id) -> HttpResponse:
    """The per-row report as CSV — TODO 1.10.7.

    Goes through ``csv_report_response``, so the download is audited and its
    cells are neutralised against spreadsheet formula injection — which matters
    more here than on any other export, because every value in it came from
    somebody else's file.
    """
    actor = request.actor
    import_run = get_object_or_404(
        ImportRun.objects.for_actor(actor).select_related("dojo", "dojo__organization"),
        pk=run_id,
    )
    require_import_permission(actor, import_run.dojo)

    return csv_report_response(
        filename=f"import-{import_run.kind}-{import_run.created_at:%Y%m%d-%H%M}.csv",
        header=["Row", "Outcome", "Source key", "Detail"],
        rows=engine.report_rows(import_run),
        actor=actor,
    )

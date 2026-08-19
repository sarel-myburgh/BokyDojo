"""Adding and removing a member of staff's grade — plan §3."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.identity.models import Person
from apps.identity.permissions import Action, PermissionDenied
from apps.identity.profiles import administers_person, person_page_context

from .grade_forms import StaffGradeForm
from .models import StaffGrade


def _person_or_403(request, person_id) -> Person:
    person = get_object_or_404(Person.objects.for_actor(request.actor), pk=person_id)
    # ⚠ administers_person, not may_edit_person. The latter lets everybody edit
    # themselves, which for a rank means an instructor typing themselves a 5th
    # dan on their own page.
    if not administers_person(request.actor, person):
        raise PermissionDenied(action=Action.ROLE_ASSIGN, actor=request.actor)
    return person


@login_required
@require_POST
def staff_grade_add_view(request, person_id):
    person = _person_or_403(request, person_id)
    form = StaffGradeForm(request.POST, actor=request.actor, person=person)

    if form.is_valid():
        grade = form.save(commit=False)
        grade.person = person
        try:
            # ⚠ validate_unique and validate_constraints are off deliberately:
            # both evaluate querysets with no tenant scope and raise
            # UnscopedAccessError. The form has already checked for a duplicate
            # with a scoped query, and the database constraint is the backstop.
            grade.full_clean(validate_unique=False, validate_constraints=False)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            grade.save()
            messages.success(request, _("Grade recorded."))
            return redirect("person-detail", person_id=person.pk)

    context = person_page_context(person=person, actor=request.actor)
    context["grade_form"] = form
    return render(request, "identity/person_detail.html", context)


@login_required
@require_POST
def staff_grade_delete_view(request, person_id, grade_id):
    person = _person_or_403(request, person_id)
    grade = get_object_or_404(
        StaffGrade.objects.for_actor(request.actor).filter(person=person), pk=grade_id
    )
    grade.delete()
    messages.success(request, _("Grade removed."))
    return redirect("person-detail", person_id=person.pk)

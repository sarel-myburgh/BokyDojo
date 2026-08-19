"""The help section — plan §3.

⚠ Written for somebody who has never used a system like this and would not
describe themselves as technical. The rules the copy follows:

* No jargon that the interface itself does not use. No "entity", "record",
  "sync", "credential", "RBAC", "tenant".
* Every guide is a numbered list of things to click, in the order you click
  them, naming the button exactly as it is labelled on screen.
* Say what a thing is *for* before saying how to do it. Somebody who does not
  know why they would want a style track will not follow the steps.
* Where something cannot be done yet, say so plainly rather than leaving the
  reader hunting for a button that does not exist.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

#: ⚠ Each guide names the page it is about with a URL name, so a route that is
#: renamed or removed breaks the help at reverse() time rather than quietly
#: sending people to a dead link. A test walks all of them.
GUIDES = [
    {
        "slug": "signing-in",
        "title": _("Signing in and passwords"),
        "blurb": _(
            "Getting into BokyDojo, changing your password, and what to do if you are locked out."
        ),
        "sections": [
            {
                "heading": _("Changing your password"),
                "url_name": "password-change",
                "link_text": _("Open the password page"),
                "steps": [
                    _("Tap the round button at the top right of the screen."),
                    _("Choose Security."),
                    _("Choose Change password."),
                    _("Type your current password once, then your new one twice."),
                ],
                "note": _(
                    "Your new password must be at least 12 characters. A short sentence you will remember, like 'my dog eats socks', is both easier and safer than something like 'P@ssw0rd!'."
                ),
            },
            {
                "heading": _("Somebody cannot get in"),
                "url_name": "org-settings",
                "link_text": _("Open Organization settings"),
                "steps": [
                    _("Go to Settings and find them in the Staff list."),
                    _("Tap their name."),
                    _("Under Signing in, tap Issue a temporary password."),
                    _("Write down the words it shows you and give them to that person."),
                    _(
                        "They sign in with those words, and the system immediately asks them to pick their own password."
                    ),
                ],
                "note": _(
                    "The temporary password is shown once and never again. If you lose it before handing it over, just issue another one."
                ),
            },
        ],
    },
    {
        "slug": "two-step",
        "title": _("Two-step verification"),
        "blurb": _("The second lock on your account, using an app on your phone."),
        "sections": [
            {
                "heading": _("What it is and why bother"),
                "url_name": "mfa-setup",
                "link_text": _("Set up two-step verification"),
                "steps": [
                    _("A password can be guessed, reused, or shoulder-surfed."),
                    _(
                        "Two-step verification adds a 6-digit number that changes every 30 seconds and lives only on your phone."
                    ),
                    _(
                        "Somebody who steals your password still cannot get in without your phone in their hand."
                    ),
                ],
                "note": _(
                    "It is not compulsory, but if your account can see people's personal details, it is worth the two minutes. The setup page has full step-by-step instructions under 'What's this?'."
                ),
            },
        ],
    },
    {
        "slug": "attendance",
        "title": _("Taking attendance"),
        "blurb": _("Marking who came to class, on a phone at the door or afterwards."),
        "sections": [
            {
                "heading": _("Marking a class as it happens"),
                "url_name": "today",
                "link_text": _("Open Today"),
                "steps": [
                    _("Tap Today. It lists the classes happening today."),
                    _("Tap the class you are teaching."),
                    _("Tap each student who is here. Tapping again undoes it."),
                    _("There is no save button — each tap is saved as you make it."),
                ],
                "note": _(
                    "If you lose signal, keep tapping. Marks are held on the device and sent as soon as you are back online."
                ),
            },
            {
                "heading": _("Letting students check themselves in"),
                # ⚠ 'today', not 'kiosk'. The check-in screen belongs to one
                # class and its address needs that class, so there is no general
                # link to it — you reach it from the class itself.
                "url_name": "today",
                "link_text": _("Open Today"),
                "steps": [
                    _("Tap Today and open the class you are about to teach."),
                    _(
                        'Tap the button that says "Let students check themselves in", then hand the tablet or phone to the students.'
                    ),
                    _("They tap their own photo or name as they come in."),
                    _(
                        "The screen stays locked to check-in — nobody can wander into the rest of the system from it."
                    ),
                    _("To get out, tap Exit and type your password."),
                ],
                "note": _(
                    "Best for a queue at the door. Keep an eye on it — it is a convenience, not a security control."
                ),
            },
            {
                "heading": _("Forgot to mark a class"),
                "url_name": "catch-up",
                "link_text": _("Open Catch up"),
                "steps": [
                    _("Tap Today, then Catch up attendance."),
                    _("It lists recent classes nobody marked."),
                    _("Tap one and mark it as normal."),
                ],
                "note": None,
            },
        ],
    },
    {
        "slug": "students",
        "title": _("Students"),
        "blurb": _("Adding people, their guardians, their grades, and their photos."),
        "sections": [
            {
                "heading": _("Adding a student"),
                "url_name": "student-create",
                "link_text": _("Add a student"),
                "steps": [
                    _("Tap Students, then Add a student."),
                    _("Fill in their name and date of birth. Everything else can wait."),
                    _("Choose the dojo they train at."),
                    _("Save. The styles that dojo teaches are applied to them automatically."),
                ],
                "note": _(
                    "Date of birth matters more than it looks: it decides whether they go on the junior or adult belt ladder."
                ),
            },
            {
                "heading": _("Adding a parent or guardian"),
                "url_name": "student-list",
                "link_text": _("Open Students"),
                "steps": [
                    _("Open the student, then find the Guardians section."),
                    _("Tap Add guardian and fill in their details."),
                    _(
                        "Say whether that person has custody — it decides who may be contacted and who may collect the child."
                    ),
                ],
                "note": _(
                    "Parents cannot sign in yet. The parent portal is still being built, so for now guardian details are for your reference and contact only."
                ),
            },
            {
                "heading": _("Recording a grading"),
                "url_name": "student-list",
                "link_text": _("Open Students"),
                "steps": [
                    _("Open the student and find their style, under Grades."),
                    _("Tap grade next to the style they were graded in."),
                    _("Choose the new belt and the date it was awarded."),
                    _("Save."),
                ],
                "note": _(
                    "You can only pick belts that are on that student's own ladder, and the system records who awarded it and when. That is deliberate — a grading is a record, not a note."
                ),
            },
            {
                "heading": _("Adding a student's photo"),
                "url_name": "student-list",
                "link_text": _("Open Students"),
                "steps": [
                    _("Open the student and find the Photo section."),
                    _("You will be asked for photo consent first if it has not been given."),
                    _("Once consent is recorded, upload the photo."),
                ],
                "note": _(
                    "A student's photo cannot be stored without consent on file, and it stops being visible the moment consent is withdrawn. This is not an inconvenience to work around — it is the reason you are allowed to hold children's photographs at all."
                ),
            },
        ],
    },
    {
        "slug": "staff",
        "title": _("Staff and what they can see"),
        "blurb": _("Adding instructors and admins, and controlling what each of them can do."),
        "sections": [
            {
                "heading": _("Adding a member of staff"),
                "url_name": "staff-create",
                "link_text": _("Add staff"),
                "steps": [
                    _("Go to Settings."),
                    _("In the Staff section, tap ADD STAFF."),
                    _("Fill in their name and email — the email is what they sign in with."),
                    _(
                        "Tick every role that applies. Somebody can be both an admin and an instructor."
                    ),
                    _("Choose whether the roles apply to one dojo or the whole organisation."),
                ],
                "note": _(
                    "Then issue them a temporary password from their page so they can sign in."
                ),
            },
            {
                "heading": _("Changing what somebody can do"),
                "url_name": "org-settings",
                "link_text": _("Open Organization settings"),
                "steps": [
                    _("Go to Settings and tap their name in the Staff list."),
                    _("Under Roles, add a role or tap revoke to take one away."),
                ],
                "note": _(
                    "Revoking keeps the history — the system remembers who held what and until when, which is exactly what you will be asked months later if something goes wrong."
                ),
            },
        ],
    },
    {
        "slug": "setup",
        "title": _("Setting up your organisation"),
        "blurb": _("Dojos, styles, and belts — the things to get right before anything else."),
        "sections": [
            {
                "heading": _("What a style is"),
                "url_name": "org-settings",
                "link_text": _("Open Organization settings"),
                "steps": [
                    _("A style is what you teach — Goju Ryu, Shotokan, Boxing."),
                    _(
                        "Each style has its own set of belts, and a student is graded separately in each."
                    ),
                    _("A style can be marked unranked if it does not use belts at all."),
                ],
                "note": _(
                    "Somebody training in two styles has two separate grades and neither affects the other."
                ),
            },
            {
                "heading": _("Adding a dojo"),
                "url_name": "dojo-create",
                "link_text": _("Add a dojo"),
                "steps": [
                    _("Go to Settings, then ADD A DOJO."),
                    _("Give it a name and choose its time zone."),
                    _("Tick the styles taught there."),
                ],
                "note": _(
                    "The time zone matters: a class at 18:30 is at 18:30 where the dojo is, not where you happen to be sitting."
                ),
            },
            {
                "heading": _("Setting up belts"),
                "url_name": "org-settings",
                "link_text": _("Open Organization settings"),
                "steps": [
                    _("Go to Settings and tap the style."),
                    _("Add the belts in order, lowest first."),
                    _(
                        "If juniors and adults follow different belts, make a separate ladder for each."
                    ),
                ],
                "note": _(
                    "Different dojos teaching the same style can use different belts — set up a ladder for that dojo and it takes precedence."
                ),
            },
        ],
    },
    {
        "slug": "importing",
        "title": _("Bringing in existing records"),
        "blurb": _("Moving students, attendance, or grades in from a spreadsheet."),
        "sections": [
            {
                "heading": _("Importing a spreadsheet"),
                "url_name": "import-wizard",
                "link_text": _("Open Import"),
                "steps": [
                    _("Save your spreadsheet as a CSV file."),
                    _("Tap Import and choose the file."),
                    _(
                        "Tell it which of your columns is the name, which is the date of birth, and so on. It will guess, and you correct it."
                    ),
                    _("It shows you exactly what would happen before anything is saved."),
                    _("If it looks right, confirm."),
                ],
                "note": _(
                    "Nothing is saved until you confirm. If a row has a problem it tells you which row and why, and the rest still come through."
                ),
            },
        ],
    },
]

GUIDES_BY_SLUG = {guide["slug"]: guide for guide in GUIDES}


@login_required
def help_index_view(request):
    return render(request, "help/index.html", {"guides": GUIDES})


@login_required
def help_guide_view(request, slug):
    from django.http import Http404

    guide = GUIDES_BY_SLUG.get(slug)
    if guide is None:
        raise Http404("No such guide.")
    return render(request, "help/guide.html", {"guide": guide})

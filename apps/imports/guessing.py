"""Guessing what a column means — TODO 1.10.1, groundwork for 1.10.6.

An operator facing twenty unmapped columns from a Gymdesk export will map three
and give up. Guessing well is most of what makes the importer feel like a tool
rather than a form.

⚠ **A guess is a default, never a decision.** Every guess is shown in an editable
select on the mapping step, and the operator sees the first rows of their own
file beside it. The importer must not quietly decide that "Contact" meant the
parent's email — it must propose that and be overruled in one click.

This is deliberately name-matching only, not content sniffing. A column called
``Notes`` full of dates is a mess the operator has to look at; a program that
reassigns it based on its contents is a program that surprises people.
"""

from __future__ import annotations

import re

#: importer field → the header spellings seen in the wild. Compared after
#: normalisation, so case, punctuation and spacing do not matter.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "external_id": ("id", "studentid", "memberid", "membernumber", "externalid", "reference"),
    "given_name": ("firstname", "givenname", "forename", "first", "name"),
    "family_name": ("lastname", "familyname", "surname", "last"),
    "preferred_name": ("preferredname", "nickname", "knownas", "goesby"),
    "date_of_birth": ("dob", "dateofbirth", "birthdate", "birthday", "born"),
    "email": ("email", "emailaddress", "studentemail", "mail"),
    "phone": ("phone", "mobile", "telephone", "tel", "phonenumber", "cell"),
    "address_line1": ("address", "addressline1", "street", "streetaddress", "address1"),
    "city": ("city", "town", "suburb"),
    "country": ("country", "countrycode"),
    "status": ("status", "memberstatus", "studentstatus", "state"),
    "joined_on": ("joined", "joinedon", "joindate", "startdate", "started", "enrolled"),
    "guardian_given_name": (
        "parentfirstname",
        "guardianfirstname",
        "parentname",
        "guardianname",
        "parentgivenname",
    ),
    "guardian_family_name": (
        "parentlastname",
        "guardianlastname",
        "parentsurname",
        "guardianfamilyname",
    ),
    "guardian_email": ("parentemail", "guardianemail", "contactemail", "parentmail"),
    "guardian_phone": ("parentphone", "guardianphone", "contactphone", "parentmobile"),
    "guardian_relationship": ("relationship", "parentrelationship", "guardianrelationship"),
    "guardian_is_primary": ("primarycontact", "isprimary", "mainconstact", "primary"),
}


def normalise(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.strip().lower())


def guess(headers: list[str], fields: dict[str, bool]) -> dict[str, str]:
    """Propose ``{source column: importer field}`` for these headers.

    ⚠ Each field is claimed at most once, first header wins. A file with both
    "Email" and "Parent email" must not map both to ``email``: the mapping
    validator rejects that outright, so a greedy guesser would hand the operator
    an error before they had done anything.
    """
    proposed: dict[str, str] = {}
    claimed: set[str] = set()

    for header in headers:
        key = normalise(header)
        if not key:
            continue
        for field, spellings in SYNONYMS.items():
            if field not in fields or field in claimed:
                continue
            if key in spellings:
                proposed[header] = field
                claimed.add(field)
                break

    # A second pass for the looser "contains" match, only for fields still
    # unclaimed. Exact matches above always win, so "Parent email" cannot steal
    # `email` from a column literally called "Email".
    for header in headers:
        if header in proposed:
            continue
        key = normalise(header)
        if not key:
            continue
        for field, spellings in SYNONYMS.items():
            if field not in fields or field in claimed:
                continue
            if any(spelling in key for spelling in spellings if len(spelling) > 3):
                proposed[header] = field
                claimed.add(field)
                break

    return proposed

"""Getting rows out of whatever the customer sends — TODO 1.10.1, plan §12.10.

⚠ **CSV cannot be sniffed.** ``apps.core.uploads.validate_upload`` works by magic
bytes, and a CSV has none — it is whatever text the exporter felt like emitting.
So this module validates by *decoding and parsing* instead, and deliberately does
not reuse the image/PDF path. Anything that reads as text and yields a header row
is accepted; anything else is refused with a message an operator can act on.

The encodings are not hypothetical. A dojo in Phnom Penh exports from Excel on
Windows and gets UTF-8 with a BOM, or cp1252 if the file has been through an
older tool. Reading either as plain UTF-8 gives ``ï»¿Given name`` as the first
column header — which then fails to match any mapping, and the operator is told
their file has no name column when it plainly does.
"""

from __future__ import annotations

import csv
import dataclasses
import io

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

#: Generous for a dojo roster — a 5000-student export of 20 columns is well under
#: this — and small enough that a mis-selected file is refused rather than
#: buffered. Import is not a file store; the bytes are never persisted.
MAX_CSV_BYTES = 10 * 1024 * 1024

#: Refused above this many data rows. The real limit is the operator's patience
#: and the request timeout; failing clearly beats timing out halfway through.
MAX_ROWS = 20_000

#: Tried in order. utf-8-sig strips a BOM if present and is identical to utf-8
#: when absent, so it is strictly the better first guess.
ENCODINGS = ("utf-8-sig", "cp1252")


class CsvRejected(ValidationError):
    """The file cannot be read as a table."""


def decode(raw: bytes) -> str:
    """Bytes → text, trying the encodings a spreadsheet actually produces."""
    if len(raw) > MAX_CSV_BYTES:
        raise CsvRejected(
            _("That file is larger than the %(limit)s MB import limit.")
            % {"limit": MAX_CSV_BYTES // (1024 * 1024)}
        )
    if not raw.strip():
        raise CsvRejected(_("That file is empty."))

    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CsvRejected(
        _("That file is not readable as text. Export it as CSV (UTF-8) and try again.")
    )


def delimiter_of(text: str) -> str:
    """Comma, semicolon or tab — a European Excel writes semicolons.

    ⚠ **Blank lines defeat ``csv.Sniffer``.** Found by running a realistic export
    through it: the sniffer reads a delimiter correctly from any prefix of the
    file but returns "Could not determine delimiter" for the whole sample,
    because one row with no delimiters at all fails its consistency check. Excel
    and hand-edited files produce those blank lines constantly. The old fallback
    to comma then parsed a semicolon file as a **single column**, and the import
    reported four rows whose only field was the entire line — a wrong answer
    delivered confidently, which is the worst kind.

    So the sample skips blank lines, and the fallback counts candidates in the
    header rather than assuming a comma.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    sample = "\n".join(lines[:50])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        pass

    header = lines[0] if lines else ""
    counts = {candidate: header.count(candidate) for candidate in ",;\t"}
    best = max(counts, key=counts.get)
    # A genuinely single-column file has none of them; comma is as good as any.
    return best if counts[best] else ","


@dataclasses.dataclass(frozen=True)
class SourceRow:
    """One data row, and where it actually sits in the operator's file.

    ⚠ ``line_number`` is the **physical** line, not the index among data rows.
    Blank lines are skipped when building this list, so the two diverge the
    moment a file contains one — and Excel adds them freely. Reporting the index
    instead sends somebody to fix row 4 and they find an empty line, while the
    row that actually failed is on line 5. Found by running a realistic export,
    not by a test.
    """

    line_number: int
    values: dict[str, str]


def read_table(raw: bytes) -> tuple[list[str], list[SourceRow]]:
    """Return ``(headers, rows)``, each row keyed by header and carrying its line.

    Headers are stripped of surrounding whitespace; values are not touched here,
    because trimming is a per-field decision the importer makes (a trailing space
    in a name is noise, in a free-text note it may not be).
    """
    text = decode(raw)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter_of(text))

    try:
        raw_headers = next(reader)
    except StopIteration as exc:
        raise CsvRejected(_("That file has no header row.")) from exc

    headers = [header.strip() for header in raw_headers]
    if not any(headers):
        raise CsvRejected(_("That file has no column names in its first row."))

    duplicates = {header for header in headers if headers.count(header) > 1 and header}
    if duplicates:
        # ⚠ Refused rather than de-duplicated. Two columns called "Phone" mean
        # the operator must say which one is wanted; silently keeping the last
        # would drop data without telling anybody.
        raise CsvRejected(
            _("These column names appear more than once: %(names)s.")
            % {"names": ", ".join(sorted(duplicates))}
        )

    rows: list[SourceRow] = []
    for values in reader:
        if not any(value.strip() for value in values):
            continue  # a blank line, which Excel adds freely at the end
        if len(rows) >= MAX_ROWS:
            raise CsvRejected(
                _("That file has more than %(limit)s rows. Split it and import in parts.")
                % {"limit": MAX_ROWS}
            )
        # zip stops at the shorter side, so a short row simply lacks those keys
        # and a long one loses its extras — both are reported per row by the
        # importer rather than failing the whole file.
        #
        # reader.line_num counts physical lines and accounts for newlines inside
        # quoted fields, which a running counter here would get wrong.
        rows.append(
            SourceRow(
                line_number=reader.line_num,
                values=dict(zip(headers, values, strict=False)),
            )
        )

    if not rows:
        raise CsvRejected(_("That file has a header row but no data."))
    return headers, rows

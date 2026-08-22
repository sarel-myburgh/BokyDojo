"""No multi-line ``{# #}`` anywhere in any template.

⚠ Django's ``{# #}`` is single-line only. Spread one over two lines and the
second line onwards renders as visible text on the page.

This has now shipped to users three times: twice on signed-in pages, and once to
the top of a public event invitation where four of them appeared above the
event's own name. Each time it was caught by a test that renders a list of
pages — and each time the page in question was not on that list, because nobody
remembered to add it.

⚠ So this one reads the templates instead of rendering them. It cannot be
defeated by forgetting to add a page, and it catches comments inside branches no
test happens to exercise — which is exactly how the last one survived a
mutation.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = sorted(ROOT.glob("templates/**/*.html"))

#: A ``{#`` with no closing ``#}`` on the same line.
UNCLOSED = re.compile(r"\{#(?![^\n]*?#\})")


def test_there_are_templates_to_check():
    """⚠ Guards the guard: a glob that matches nothing passes silently."""
    assert len(TEMPLATES) > 20


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: str(p.relative_to(ROOT)))
def test_template_has_no_multiline_comment(path):
    text = path.read_text(encoding="utf-8")

    offenders = [text[: m.start()].count("\n") + 1 for m in UNCLOSED.finditer(text)]

    assert not offenders, (
        f"{path.relative_to(ROOT)} line(s) {offenders}: "
        "{# #} is single-line only — use {% comment %}…{% endcomment %}"
    )

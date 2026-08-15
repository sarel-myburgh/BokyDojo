"""The bundled Khmer face — TODO 0.4.4, plan §13.4.

⚠ This whole file exists because 0.4.4 was ticked while the font half of it was
false: nothing was bundled, and dojo.css claimed a Google Fonts load that the
CSP's `font-src 'self'` forbids. A claim about an asset is worth nothing unless
something asserts the asset is there, so these tests parse the real @font-face
rather than trusting the comment above it.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders

DOJO_CSS = "css/dojo.css"


def _dojo_css() -> str:
    found = finders.find(DOJO_CSS)
    assert found is not None, "dojo.css itself is not servable"
    return Path(found).read_text(encoding="utf-8")


def _font_face_block() -> str:
    match = re.search(r"@font-face\s*\{[^}]*\}", _dojo_css())
    assert match is not None, "no @font-face is declared; the Khmer face is not bundled"
    return match.group(0)


def test_every_font_the_css_names_actually_resolves():
    """A url() in the stylesheet must correspond to a file the finders can serve.

    The same failure mode as the service worker shell: the page renders fine
    without its font, so nothing goes red while Khmer silently falls back.
    """
    urls = re.findall(r'url\(["\']?([^"\')]+)["\']?\)', _dojo_css())
    assert urls, "expected at least one bundled font url()"

    missing = []
    for url in urls:
        # Relative to static/css/, which is where dojo.css is served from.
        resolved = (Path("css") / url).as_posix()
        resolved = str(Path(resolved).resolve().relative_to(Path.cwd().resolve()))
        if finders.find(resolved) is None:
            missing.append(url)
    assert not missing, f"named in dojo.css but unservable: {missing}"


def test_the_khmer_font_file_is_a_real_woff2():
    """Guard against committing a 404 page or an LFS pointer under a .woff2 name."""
    found = finders.find("fonts/noto-sans-khmer-khmer.woff2")
    assert found is not None, "the bundled Khmer woff2 is missing"

    data = Path(found).read_bytes()
    assert data[:4] == b"wOF2", f"not a woff2 file: starts with {data[:4]!r}"
    assert len(data) > 10_000, f"implausibly small for a Khmer face: {len(data)} bytes"


def test_the_font_is_self_hosted_because_the_csp_forbids_a_cdn():
    """⚠ The exact bug 0.4.4 shipped: a font URL pointing at a host the CSP blocks.

    `font-src 'self'` means an external font silently fails to load, and the only
    symptom is Khmer rendering in whatever the OS happens to have.
    """
    block = _font_face_block()
    assert "//" not in re.sub(r"/\*.*?\*/", "", block, flags=re.S), (
        "the @font-face src points at an external host; font-src 'self' blocks it"
    )


def test_the_face_is_limited_to_khmer_so_latin_pages_do_not_fetch_it():
    """Without unicode-range every English-only page downloads ~59KB for nothing."""
    block = _font_face_block()
    assert "unicode-range" in block, "no unicode-range: every page would fetch the face"
    assert "U+1780-17FF" in block, "the Khmer block itself is not in the declared range"


def test_the_face_does_not_block_text_from_rendering():
    """An instructor on a bad connection must see the roster, not blank space."""
    assert "font-display: swap" in _font_face_block()


def test_the_open_font_licence_travels_with_the_font():
    """OFL 1.1 requires the licence to be distributed alongside the font."""
    found = finders.find("fonts/OFL.txt")
    assert found is not None, "OFL.txt is missing; the OFL requires it ship with the font"

    licence = Path(found).read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE" in licence.upper()


def test_the_bundled_family_is_the_one_the_font_stack_asks_for():
    """A bundled face nothing references is dead weight.

    The @font-face family name has to match a name in the Tailwind stack, or the
    browser never looks at the bundled file and quietly uses an OS font instead.
    """
    family = re.search(r'font-family:\s*"([^"]+)"', _font_face_block())
    assert family is not None, "the @font-face declares no font-family"

    config = Path(settings.BASE_DIR, "tailwind.config.cjs").read_text(encoding="utf-8")
    assert f'"{family.group(1)}"' in config, (
        f"{family.group(1)!r} is bundled but absent from the tailwind font stack"
    )

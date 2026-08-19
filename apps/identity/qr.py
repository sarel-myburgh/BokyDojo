"""QR codes for authenticator enrolment — plan §0.6.

⚠ Rendered as inline SVG markup, not an <img> pointing anywhere.

Two reasons. A remote QR service would be handed the provisioning URI, which
*contains the TOTP secret* — that is the entire second factor, posted to a third
party. And the CSP forbids loading images from another host anyway. Inline SVG
needs no img-src grant, no data: URI, and no extra request.
"""

from __future__ import annotations

import io

import segno
from django.utils.safestring import mark_safe


def svg_for(uri: str, *, scale: int = 5) -> str:
    """An <svg> element for ``uri``, safe to drop straight into a template.

    ⚠ ``mark_safe`` is sound here only because nothing user-supplied reaches the
    output: segno emits its own SVG from the QR matrix, and every path
    coordinate it writes is a number it computed. The URI is encoded into
    modules, never echoed into the markup.
    """
    buffer = io.BytesIO()
    segno.make(uri, error="m").save(
        buffer,
        kind="svg",
        scale=scale,
        border=2,
        # No XML declaration or DOCTYPE — this is embedded in an HTML page, not
        # served as a standalone document.
        xmldecl=False,
        svgns=True,
        omitsize=True,
        svgclass=None,
        lineclass=None,
    )
    return mark_safe(buffer.getvalue().decode("utf-8"))  # noqa: S308 — see the docstring

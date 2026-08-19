"""What build is this — plan §3.

⚠ Exists because "I pulled the image and the old behaviour is still there" is
otherwise unanswerable from the screen. Pulling an image does not restart a
running container, and a stale container looks exactly like a bad deploy. The
badge in the corner tells you which build you are actually looking at, so that
question is settled by looking rather than by guessing.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache

#: Bumped by hand. Not derived from git tags: this is the number a person quotes
#: in a bug report, and it should not change every commit.
VERSION = "0.1"


@lru_cache(maxsize=1)
def build_revision() -> str:
    """The short commit this build came from, or "dev" when it is not known.

    In the container this is baked in at build time by the Dockerfile. Running
    from a checkout it is read from git once, and never again — this is called
    on every page render.
    """
    baked = os.environ.get("BOKYDOJO_REVISION", "").strip()
    if baked:
        return baked[:7]

    try:
        result = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short=7", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=2,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        )
    except (OSError, subprocess.SubprocessError):
        return "dev"
    if result.returncode != 0:
        return "dev"
    return result.stdout.strip() or "dev"


def display_version() -> str:
    """What the badge shows: "v0.1 (9ef17cc)"."""
    return f"v{VERSION} ({build_revision()})"

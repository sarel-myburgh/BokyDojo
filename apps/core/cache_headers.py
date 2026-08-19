"""Stop the browser serving a stale — or somebody else's — page.

⚠ Signed-in pages carried no ``Cache-Control`` at all. Django only sets one on
views marked ``@never_cache``; everything else went out with nothing but
``Vary``. A response with no freshness information is not "uncacheable": the
browser applies heuristic freshness and may reuse it for a long time, which is
why a deployed change appeared not to land until somebody cleared their history.

⚠ The staleness was the visible half. The other half is that those pages contain
names, medical notes and safeguarding records, and nothing told the browser — or
any proxy between it and the server — not to keep them. Pressing Back after
signing out on a shared computer is enough to bring one up.
"""

from __future__ import annotations

#: Prefixes served as immutable-ish assets rather than pages. Django does not
#: serve these in production (Caddy does), but it does in development, and the
#: rule should be the same in both.
_ASSET_PREFIXES = ("/static/", "/media/")


class NoStoreMiddleware:
    """Mark every application response uncacheable unless it says otherwise.

    ⚠ Deliberately does not overwrite an existing ``Cache-Control``. Several
    views set their own deliberately — the document and photograph endpoints
    already send ``private, no-store`` — and a blanket overwrite here would make
    those decisions invisible and unmaintainable.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.path.startswith(_ASSET_PREFIXES):
            return response
        if response.has_header("Cache-Control"):
            return response

        response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response["Pragma"] = "no-cache"
        return response

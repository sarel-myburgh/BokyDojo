"""Strict Content Security Policy with per-response nonces — TODO 0.6.7."""

from __future__ import annotations

import secrets


def csp_nonce(request):
    return {"csp_nonce": getattr(request, "csp_nonce", "")}


class ContentSecurityPolicyMiddleware:
    """Attach a restrictive CSP; templates may nonce exceptional inline assets."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(18)
        response = self.get_response(request)
        if "Content-Security-Policy" not in response:
            nonce = request.csp_nonce
            response["Content-Security-Policy"] = "; ".join(
                (
                    "default-src 'self'",
                    "base-uri 'self'",
                    "object-src 'none'",
                    "frame-ancestors 'none'",
                    "form-action 'self'",
                    f"script-src 'self' 'nonce-{nonce}'",
                    f"style-src 'self' 'nonce-{nonce}'",
                    "img-src 'self' data:",
                    "font-src 'self'",
                    "connect-src 'self'",
                    "manifest-src 'self'",
                    "worker-src 'self'",
                )
            )
        return response

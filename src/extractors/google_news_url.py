from __future__ import annotations

from typing import Any

from googlenewsdecoder import gnewsdecoder


def resolve_google_news_url(url: str) -> dict[str, Any]:
    try:
        result = gnewsdecoder(url)
    except Exception as exc:
        return {
            "resolved_url": "",
            "resolve_status": "resolve_failed",
            "resolve_error": str(exc),
        }

    if result.get("status") and result.get("decoded_url"):
        return {
            "resolved_url": result["decoded_url"],
            "resolve_status": "success",
            "resolve_error": "",
        }

    return {
        "resolved_url": "",
        "resolve_status": "resolve_failed",
        "resolve_error": result.get("message", "Google News URL could not be decoded."),
    }

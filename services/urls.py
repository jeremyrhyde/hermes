"""URL canonicalization for duplicate detection.

Per research finding R3, stripping tracking parameters before the uniqueness
check is the one dedup mechanism with verified prior art. This collapses the
SAME url under different tags; it does NOT detect the same story republished at
a different URL, which is explicitly out of scope (spec section 4).

The strip list is deliberately conservative. Removing a parameter that actually
selects content (``?page=2``) would merge distinct articles, which is worse
than leaving a duplicate.

The tracking params are split into two tiers because that same asymmetry
applies unevenly across hosts:

- Universal tags (``utm_*``, ``fbclid``, ``gclid``, ...) are unambiguously
  analytics noise everywhere, so they're stripped on every host.
- Substack's own share tags (``post_id``, ``publication_id``, ...) are
  redundant on Substack, where the slug in the path already identifies the
  article — but they're generic parameter names that could be the *only*
  content selector on some other site (``?post_id=123`` might be that site's
  actual routing param). Stripping them there would silently merge distinct
  articles into one row, which is strictly worse than leaving a duplicate.
  So they're only stripped when the host is (or is a subdomain of)
  ``substack.com``.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PREFIXES = ("utm_",)

# Stripped on every host: unambiguous analytics/click-tracking tags.
UNIVERSAL_TRACKING_PARAMS = frozenset(
    {
        "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "twclid",
        "igshid", "mc_cid", "mc_eid", "yclid", "_hsenc", "_hsmi",
        "vero_id", "vero_conv", "oly_anon_id", "oly_enc_id",
    }
)

# Stripped only when the host is substack.com or a subdomain of it.
SUBSTACK_TRACKING_PARAMS = frozenset(
    {"publication_id", "post_id", "isfreemail", "triedredirect"}
)


def _is_substack_host(netloc: str) -> bool:
    # netloc may carry "user:pass@" and/or ":port"; strip both before matching.
    host = netloc.rsplit("@", 1)[-1].rsplit(":", 1)[0]
    return host == "substack.com" or host.endswith(".substack.com")


def _is_tracking(key: str, *, is_substack: bool) -> bool:
    lowered = key.lower()
    if lowered.startswith(TRACKING_PREFIXES) or lowered in UNIVERSAL_TRACKING_PARAMS:
        return True
    return is_substack and lowered in SUBSTACK_TRACKING_PARAMS


def canonicalize_url(url: str) -> str:
    """Return *url* with tracking noise removed, for use as a dedup key.

    Normalizes scheme and host to lowercase, drops the fragment, strips a
    trailing slash from non-root paths, and removes known tracking parameters
    while preserving parameter order for everything else.
    """

    parts = urlsplit(url.strip())
    netloc = parts.netloc.lower()
    is_substack = _is_substack_host(netloc)

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking(k, is_substack=is_substack)
    ]

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit(
        (
            parts.scheme.lower(),
            netloc,
            path,
            urlencode(kept),
            "",  # fragment always dropped
        )
    )

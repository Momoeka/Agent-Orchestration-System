"""Error taxonomy (Rules.md §3): what a caller may retry vs what is a config/code defect."""

from __future__ import annotations


class LibrarianError(Exception):
    """Base for everything this library raises on purpose."""


class RetryableError(LibrarianError):
    """Transient: rate limits, timeouts, 5xx. A caller (or chain) may retry or back off."""


class NonRetryableError(LibrarianError):
    """Permanent for this request: bad input, auth, missing config. Retrying cannot help."""

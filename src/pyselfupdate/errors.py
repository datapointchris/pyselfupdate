"""Exceptions raised by pyselfupdate.

Every failure mode has its own class so callers match on a type rather than on
message text. Every class derives from `SelfUpdateError`, which is what a caller
catches when it does not care why an update did not happen. A class that narrows
another derives from it, so catching `SourceError` still catches a rate limit.
"""

from __future__ import annotations

import datetime as dt


class SelfUpdateError(Exception):
    """Base class for every error this package raises."""


class InvalidConfigError(SelfUpdateError):
    """A Config is missing a required field."""


class LocalInstallError(SelfUpdateError):
    """The running build is not a release, so no release can be compared against it.

    Raised for an install from a local path or an editable checkout, for a git
    install from a branch or a commit rather than a release tag, and for a
    running version that is not a semantic version. Reinstalling would replace
    the build with a release that may be older, with no way to tell which is
    newer. This is the analog of goselfupdate's ErrDevBuild.
    """


class NotInstalledError(SelfUpdateError):
    """uv's receipt is missing, unreadable, or names no requirement for the tool.

    `update` raises it too when the receipt lists a `--with` requirement that no
    requirements-file line reproduces, because reinstalling would drop it.
    """


class NoReleaseError(SelfUpdateError):
    """The source publishes no usable release."""


class SourceError(SelfUpdateError):
    """The release source could not be reached or returned something unusable."""


class SelfUpdateSourceFailure(SourceError):
    """A transport or protocol failure, as opposed to a missing release.

    Distinct from NoReleaseError so a caller can tell "GitHub is unreachable"
    apart from "this repository has published nothing", which are the same HTTP
    status for a private repository and want different messages.
    """


class RateLimitedError(SelfUpdateSourceFailure):
    """The source's rate limit for this caller is spent.

    `authenticated` says which ceiling was hit. Without a credential the limit
    is shared by every host behind one address, and supplying a token lifts it.
    With one, nothing is left to configure, and the caller can only wait.
    `resets_at` is when the ceiling lifts, in UTC, or None when the response
    did not say.
    """

    def __init__(self, message: str, *, authenticated: bool, resets_at: dt.datetime | None = None) -> None:
        super().__init__(message)
        self.authenticated = authenticated
        self.resets_at = resets_at


class InstallFailedError(SelfUpdateError):
    """The install command ran and failed."""


class LockUnreadableError(SelfUpdateError):
    """The release's `uv.lock` could not be read, so nothing was installed.

    Raised when git or uv is not on PATH, the tag will not clone, or `uv export`
    refuses the lock. A tag with no `uv.lock` at all is not this error: it
    installs unlocked and sets `Result.lock_missing`.
    """

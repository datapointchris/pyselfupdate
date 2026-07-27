"""Exceptions raised by pyselfupdate.

Every failure mode has its own class so callers match on a type rather than on
message text. The hierarchy is one level deep under `SelfUpdateError`, which is
what a caller catches when it does not care why an update did not happen.
"""


class SelfUpdateError(Exception):
    """Base class for every error this package raises."""


class InvalidConfigError(SelfUpdateError):
    """A Config is missing a required field."""


class LocalInstallError(SelfUpdateError):
    """The tool was installed from a local path or as an editable checkout.

    Reinstalling would discard a working copy for a release that may be older,
    with no way to tell which is newer. This is the analogue of goselfupdate's
    ErrDevBuild.
    """


class NotInstalledError(SelfUpdateError):
    """The tool is not installed as a uv tool, so there is nothing to update."""


class NoReleaseError(SelfUpdateError):
    """The source publishes no usable release."""


class SourceError(SelfUpdateError):
    """The release source could not be reached or returned something unusable."""


class InstallFailedError(SelfUpdateError):
    """The install command ran and failed."""

"""Checking for and applying updates."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from pyselfupdate import version as semver
from pyselfupdate.config import Config
from pyselfupdate.errors import LocalInstallError
from pyselfupdate.errors import NoReleaseError
from pyselfupdate.install import Installation
from pyselfupdate.install import InstallKind
from pyselfupdate.install import read_installation
from pyselfupdate.install import reexec
from pyselfupdate.install import requirement_for
from pyselfupdate.install import run_install
from pyselfupdate.source import Changeloger
from pyselfupdate.source import Release


@dataclass(frozen=True)
class Result:
    """What an update found, whether or not it installed anything."""

    # The running version, canonicalized with a leading "v".
    current: str

    # The version now installed, or the one `update` would install after a
    # `check`. Equals `current` when nothing is newer.
    latest: str

    applied: bool = False
    release: Release | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def update_available(self) -> bool:
        return self.current != self.latest


def check(config: Config) -> Result:
    """Report whether a newer release exists, without installing anything."""
    resolved = config.resolved()
    current = _current_version(resolved)

    release = resolved.require_source().latest_release()
    latest = semver.canonical(release.tag)
    if not semver.is_valid(latest):
        raise NoReleaseError(f'tag {release.tag!r} is not a semantic version')

    if semver.compare(latest, current) <= 0:
        return Result(current=current, latest=current)

    return Result(current=current, latest=latest, release=release)


def require_updatable(config: Config) -> Installation:
    """The installation an update would rewrite, refusing one that must not be.

    Public because it is the only part of an update that costs nothing and can
    still refuse outright. A caller that orders the steps itself -- as
    `typercmd.run_update` does, to fetch a changelog while the environment is
    still intact -- keeps the refusal ahead of the network by starting here.
    """
    installation = read_installation(config.resolved().tool)
    _require_updatable(installation)
    return installation


def install_release(config: Config, result: Result, installation: Installation, *, quiet: bool = True) -> Result:
    """Install the release `result` names, over the running one.

    A no-op returning the result unchanged when there is nothing newer. On
    success this interpreter's environment has been rewritten underneath it, so
    the caller may not import anything afterwards -- see
    `pyselfupdate.install.reexec` and `pyselfupdate.install.exit_now`.
    """
    if not result.update_available or result.release is None:
        return result

    requirement = requirement_for(installation, result.release.install_ref())
    run_install(requirement, quiet=quiet)

    return Result(
        current=result.current,
        latest=result.latest,
        applied=True,
        release=result.release,
    )


def update(config: Config, *, quiet: bool = True) -> Result:
    """Check for a newer release and install it, in one call.

    A no-op returning `applied=False` when already current. The composition of
    `require_updatable`, `check` and `install_release`, in the order that keeps
    a refusal cheap; a caller needing to do work between the check and the
    install calls those three itself.
    """
    resolved = config.resolved()
    installation = require_updatable(resolved)
    result = check(resolved)
    return install_release(resolved, result, installation, quiet=quiet)


def update_and_reexec(config: Config, *, quiet: bool = True) -> Result:
    """`update`, then replace this process when anything was installed.

    Returns normally only when nothing was installed; otherwise it does not
    return at all.
    """
    result = update(config, quiet=quiet)
    if result.applied:
        reexec()
    return result


def changelog(config: Config, from_version: str, to_version: str) -> list[str]:
    """Commit subjects between two versions.

    Returns an empty list when the source cannot produce one, which is not an
    error: a missing changelog must never fail an update that succeeded.
    """
    source = config.resolved().require_source()
    if not isinstance(source, Changeloger):
        return []
    return source.changelog(from_version, to_version)


def _current_version(config: Config) -> str:
    """The running version, canonicalized, rejecting one that is not a release.

    An install that tracks a git branch rather than a tag reports whatever
    version was in pyproject.toml when it was built, which says nothing about
    how far behind the checkout is. Comparing it against a tag would claim an
    update is available on every run, or none ever.
    """
    canonical = semver.canonical(config.version)
    if not semver.is_valid(canonical):
        raise LocalInstallError(f'{config.tool} reports version {config.version!r}, which is not a release version')
    return canonical


def _require_updatable(installation: Installation) -> None:
    if installation.kind is InstallKind.LOCAL:
        raise LocalInstallError(
            f'{installation.tool} is installed from {installation.url}; update the checkout instead of reinstalling over it'
        )
    if installation.kind is InstallKind.GIT and not installation.revision:
        raise LocalInstallError(
            f'{installation.tool} is installed from a branch rather than a tag, '
            f'so a release version cannot be compared against it; '
            f'reinstall from a tagged release to enable updates'
        )

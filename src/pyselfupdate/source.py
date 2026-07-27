"""Where releases come from.

`Source` is the seam that keeps the rest of the package off the network in
tests, and that lets a caller publish releases somewhere other than GitHub.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Protocol
from typing import runtime_checkable


@dataclass(frozen=True)
class Release:
    """One published release."""

    tag: str
    url: str = ''
    notes: str = ''

    # The reference to install. For a git source this is what goes after `@` in
    # the requirement; it is kept separate from `tag` because a repository
    # publishing several streams tags them with a prefix that the installer has
    # to reproduce exactly while the version shown to a user should not carry it.
    ref: str = ''
    metadata: dict[str, str] = field(default_factory=dict)

    def install_ref(self) -> str:
        return self.ref or self.tag


@runtime_checkable
class Source(Protocol):
    """Locates the newest release."""

    def latest_release(self) -> Release:
        """The newest release, or raise NoReleaseError when there is none."""
        ...


@runtime_checkable
class Changeloger(Protocol):
    """A source that can describe what changed between two versions.

    Separate from `Source` because it is optional: a source that cannot produce
    one is not broken, and callers degrade to printing nothing.
    """

    def changelog(self, from_ref: str, to_ref: str) -> list[str]: ...

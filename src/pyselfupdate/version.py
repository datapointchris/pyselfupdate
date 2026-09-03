"""Semantic version comparison.

Implemented here rather than taken from `packaging` so that this package has no
third-party dependencies. `packaging` also implements PEP 440, whose precedence
rules differ from semver's in ways that matter: it normalizes `1.0.0-rc.1` to
`1.0.0rc1` and orders `1.0.0.post1` above `1.0.0`, neither of which describes a
git tag.

Precedence follows https://semver.org section 11, and is byte-for-byte the same
behavior as goselfupdate's semver.go so that the two libraries never disagree
about which of two releases is newer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

IDENTIFIER_PATTERN = re.compile(r'^[0-9A-Za-z-]+$')
NUMERIC_PATTERN = re.compile(r'^[0-9]+$')


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: str


def parse(text: str) -> Version | None:
    """Parse a version, or None when the text is not one.

    Accepts an optional leading `v` and one to three numeric components, so
    `v1`, `1.2` and `v1.2.3-rc.1+build` all parse. Absent components are zero.
    """
    text = text.removeprefix('v')
    if not text:
        return None

    # Build metadata is ignored entirely for precedence.
    plus = text.find('+')
    if plus >= 0:
        text = text[:plus]

    prerelease = ''
    dash = text.find('-')
    if dash >= 0:
        prerelease = text[dash + 1 :]
        text = text[:dash]
        if not is_valid_prerelease(prerelease):
            return None

    fields = text.split('.')
    if len(fields) > 3:
        return None

    numbers = []
    for field in fields:
        number = parse_numeric(field)
        if number is None:
            return None
        numbers.append(number)
    numbers.extend([0] * (3 - len(numbers)))

    return Version(numbers[0], numbers[1], numbers[2], prerelease)


def parse_numeric(field: str) -> int | None:
    """A numeric component, rejecting the leading zeros the spec disallows."""
    if not NUMERIC_PATTERN.match(field):
        return None
    if len(field) > 1 and field[0] == '0':
        return None
    return int(field)


def is_valid_prerelease(prerelease: str) -> bool:
    if not prerelease:
        return False
    for identifier in prerelease.split('.'):
        if not IDENTIFIER_PATTERN.match(identifier):
            return False
        # A numeric identifier may not carry leading zeros.
        if NUMERIC_PATTERN.match(identifier) and len(identifier) > 1 and identifier[0] == '0':
            return False
    return True


def is_valid(text: str) -> bool:
    return parse(text) is not None


def canonical(text: str) -> str:
    """Add the leading `v` this package prints versions with.

    Tags carry one and `importlib.metadata` does not, so both forms reach this
    package and have to be reported the same way.
    """
    if not text or text.startswith('v'):
        return text
    return f'v{text}'


def compare(a: str, b: str) -> int:
    """Return -1, 0 or 1 as `a` is less than, equal to, or greater than `b`.

    Invalid versions sort below valid ones.
    """
    left = parse(a)
    right = parse(b)
    if left is right is None:
        return 0
    if left is None:
        return -1
    if right is None:
        return 1

    left_core = (left.major, left.minor, left.patch)
    right_core = (right.major, right.minor, right.patch)
    if left_core != right_core:
        return -1 if left_core < right_core else 1

    return compare_prerelease(left.prerelease, right.prerelease)


def compare_prerelease(a: str, b: str) -> int:
    """Precedence for pre-release identifiers.

    A version with a pre-release ranks below the same version without one,
    numeric identifiers compare numerically and rank below alphanumeric ones,
    and where all preceding identifiers are equal the longer list wins.
    """
    if a == b:
        return 0
    if not a:
        return 1
    if not b:
        return -1

    left = a.split('.')
    right = b.split('.')

    # Indexed rather than zipped because the lists are allowed to differ in
    # length -- the shorter one losing is the rule implemented just below -- and
    # every spelling of that with zip fails a linter: bare zip trips ruff's
    # B905, strict=True is wrong, and strict=False trips refurb's FURB120. This
    # also mirrors goselfupdate's loop exactly.
    for index in range(min(len(left), len(right))):
        result = compare_identifier(left[index], right[index])
        if result != 0:
            return result

    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def compare_identifier(a: str, b: str) -> int:
    a_numeric = bool(NUMERIC_PATTERN.match(a))
    b_numeric = bool(NUMERIC_PATTERN.match(b))

    if a_numeric and b_numeric:
        # Compared as integers rather than strings so that 2 ranks below 11.
        left = int(a)
        right = int(b)
        if left == right:
            return 0
        return -1 if left < right else 1
    if a_numeric:
        return -1
    if b_numeric:
        return 1
    if a == b:
        return 0
    return -1 if a < b else 1

"""Version parsing and precedence.

The ordering matrix comes from https://semver.org section 11, which is the
authority both this and goselfupdate's semver.go were written against. If these
two implementations ever disagree, a tool and its Go sibling will disagree about
which release is newer.
"""

from __future__ import annotations

import itertools

import pytest

from pyselfupdate import version as semver


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('1.2.3', (1, 2, 3, '')),
        ('v1.2.3', (1, 2, 3, '')),
        ('1.2', (1, 2, 0, '')),
        ('v1', (1, 0, 0, '')),
        ('1.2.3-rc.1', (1, 2, 3, 'rc.1')),
        ('v1.2.3-rc.1+build.5', (1, 2, 3, 'rc.1')),
        ('1.2.3+build', (1, 2, 3, '')),
        ('0.0.0', (0, 0, 0, '')),
    ],
)
def test_parses_valid_versions(text: str, expected: tuple) -> None:
    parsed = semver.parse(text)
    assert parsed is not None
    assert (parsed.major, parsed.minor, parsed.patch, parsed.prerelease) == expected


@pytest.mark.parametrize(
    'text',
    [
        '',
        'v',
        'dev',
        'latest',
        '1.2.3.4',
        '01.2.3',
        '1.02.3',
        'a.b.c',
        '1.2.3-',
        '1.2.3-rc..1',
        '1.2.3-rc.01',
        '1.2.3-rc!',
    ],
)
def test_rejects_invalid_versions(text: str) -> None:
    assert semver.parse(text) is None
    assert not semver.is_valid(text)


def test_a_go_pseudo_version_is_valid_semver() -> None:
    """Documenting a difference from goselfupdate that is easy to assume away.

    Go stamps `v1.6.1-0.20260724161156-2c04703+dirty` onto local builds, and the
    Go tools reject it with a separate `^v\\d+\\.\\d+\\.\\d+$` regex at the call
    site -- not in semver.go, because by the specification it is a perfectly
    ordinary pre-release and sorts below v1.6.1.

    pyselfupdate needs no such regex: a Python tool's dev-ness is read from uv's
    receipt, which says whether the install came from a tag, a branch or a local
    path. That is a stronger signal than any version string, and it is why
    is_valid is allowed to stay a faithful semver check here.
    """
    pseudo = 'v1.6.1-0.20260724161156-2c04703+dirty'
    assert semver.is_valid(pseudo)
    assert semver.compare(pseudo, 'v1.6.1') == -1


@pytest.mark.parametrize(
    ('text', 'expected'),
    [('1.2.3', 'v1.2.3'), ('v1.2.3', 'v1.2.3'), ('', '')],
)
def test_canonical_adds_one_leading_v(text: str, expected: str) -> None:
    assert semver.canonical(text) == expected


# https://semver.org section 11, in full.
PRECEDENCE = [
    '1.0.0-alpha',
    '1.0.0-alpha.1',
    '1.0.0-alpha.beta',
    '1.0.0-beta',
    '1.0.0-beta.2',
    '1.0.0-beta.11',
    '1.0.0-rc.1',
    '1.0.0',
    '1.0.1',
    '1.1.0',
    '2.0.0',
]


def test_precedence_follows_the_specification() -> None:
    for lower, higher in itertools.combinations(PRECEDENCE, 2):
        assert semver.compare(lower, higher) == -1, f'{lower} should sort below {higher}'
        assert semver.compare(higher, lower) == 1, f'{higher} should sort above {lower}'


def test_equal_versions_compare_equal() -> None:
    for text in PRECEDENCE:
        assert semver.compare(text, text) == 0
    assert semver.compare('1.2.3', 'v1.2.3') == 0
    assert semver.compare('1.2.3+a', '1.2.3+b') == 0, 'build metadata is ignored for precedence'


def test_numeric_prerelease_identifiers_compare_numerically() -> None:
    assert semver.compare('1.0.0-beta.2', '1.0.0-beta.11') == -1


def test_numeric_identifiers_rank_below_alphanumeric() -> None:
    assert semver.compare('1.0.0-1', '1.0.0-alpha') == -1


def test_a_longer_identifier_list_wins_when_all_else_is_equal() -> None:
    assert semver.compare('1.0.0-alpha', '1.0.0-alpha.1') == -1


def test_invalid_versions_sort_below_valid_ones() -> None:
    assert semver.compare('nonsense', '1.0.0') == -1
    assert semver.compare('1.0.0', 'nonsense') == 1
    assert semver.compare('nonsense', 'rubbish') == 0

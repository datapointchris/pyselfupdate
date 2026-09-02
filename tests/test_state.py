"""The state file's name and location.

The three self-update libraries have to agree byte-for-byte on how the machine
is derived. A disagreement puts one box's state under two names, and neither box
then sees the other's — which is the collision the machine in the name exists to
make unreachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyselfupdate import state


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('archlinux', 'archlinux'),
        ('Macmini', 'macmini'),
        ('macmini.trusted', 'macmini'),
        ('MBP.local', 'mbp'),
        ('  archlinux.lan  ', 'archlinux'),
        ('box.example.com', 'box'),
        ('', 'unknown'),
        ('   ', 'unknown'),
        ('.leading-dot', 'unknown'),
    ],
)
def test_canonical_machine_drops_the_domain_and_the_case(raw: str, expected: str) -> None:
    assert state.canonical_machine(raw) == expected


def test_the_filename_carries_the_machine() -> None:
    assert state.filename('archlinux') == 'autoupdate-archlinux.json'


def test_two_machines_write_two_files() -> None:
    assert state.filename('archlinux') != state.filename('macmini')


def test_this_machine_is_already_canonical() -> None:
    assert state.machine() == state.canonical_machine(state.machine())


def test_state_lands_under_the_tool_and_the_machine(state_home: Path) -> None:
    path = state.state_path('demo')

    assert path.parent == state_home / 'demo'
    assert path.name == f'autoupdate-{state.machine()}.json'


def test_a_write_is_read_back_from_the_same_path(state_home: Path) -> None:
    state.write(state.State(tool='demo', current_version='v1.0.0'))

    assert (state_home / 'demo' / state.filename(state.machine())).is_file()
    assert state.read('demo').current_version == 'v1.0.0'


def test_another_machines_file_is_not_read_as_this_ones(state_home: Path) -> None:
    directory = state_home / 'demo'
    directory.mkdir(parents=True)
    (directory / state.filename('someotherbox')).write_text('{"schema": 1, "tool": "demo", "current_version": "v9.9.9"}', encoding='utf-8')

    assert state.read('demo').current_version == ''

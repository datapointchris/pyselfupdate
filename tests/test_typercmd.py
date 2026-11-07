"""run_update().

The assertions that matter here are about ordering, not output: every step that
reaches the network or imports a module must happen before the install, and the
process must not return into its CLI afterwards. `run_install`, `read_lock` and
`exit_now` are all patched -- the first would rebuild a real virtual
environment, the second would clone a repository, and the last would take the
test runner down with it.
"""

from __future__ import annotations

import pytest
import typer
from conftest import StubLock
from conftest import StubSource

from pyselfupdate import Config
from pyselfupdate import typercmd
from pyselfupdate import updater as update_module
from pyselfupdate.errors import InstallFailedError
from pyselfupdate.errors import LockUnreadableError
from pyselfupdate.install import Pins
from pyselfupdate.typercmd import run_update
from pyselfupdate.updater import Result


class RecordingSource(StubSource):
    """A StubSource that appends every call to a shared event log."""

    def __init__(self, events: list[str], tag: str = 'v2.0.0', subjects: list[str] | None = None) -> None:
        super().__init__(tag=tag, subjects=subjects or [])
        self.events = events

    def changelog(self, from_ref: str, to_ref: str) -> list[str]:
        self.events.append('changelog')
        return super().changelog(from_ref, to_ref)


@pytest.fixture
def events() -> list[str]:
    return []


@pytest.fixture(autouse=True)
def lock(monkeypatch: pytest.MonkeyPatch) -> StubLock:
    stub = StubLock()
    monkeypatch.setattr(update_module, 'read_lock', stub)
    return stub


@pytest.fixture
def installs(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> list[str]:
    recorded: list[str] = []

    def record(
        requirement: str,
        quiet: bool = True,
        pins: Pins | None = None,
        with_requirements: tuple[str, ...] = (),
        source_flags: tuple[str, ...] = (),
    ) -> None:
        events.append('install')
        recorded.append(requirement)

    monkeypatch.setattr(update_module, 'run_install', record)
    return recorded


@pytest.fixture
def exits(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> list[int]:
    """Replaces the hard exit, which would otherwise end the test session."""
    recorded: list[int] = []

    def record(code: int = 0) -> None:
        events.append('exit')
        recorded.append(code)

    monkeypatch.setattr(typercmd, 'exit_now', record)
    return recorded


@pytest.fixture
def pinned(make_receipt):
    make_receipt('demo', '{ name = "demo", git = "https://github.com/x/demo.git?rev=v1.0.0" }')


def config(source: StubSource, version: str = '1.0.0') -> Config:
    return Config(tool='demo', owner='x', version=version, source=source)


def test_the_changelog_is_fetched_before_the_install(pinned, installs, exits, events) -> None:
    """A changelog fetched after the install can need a module the install deleted."""
    source = RecordingSource(events, subjects=['feat: one'])

    run_update(config(source))

    assert events == ['changelog', 'install', 'exit']


def test_an_applied_update_ends_the_process(pinned, installs, exits, capsys) -> None:
    """Returning would hand control back to typer, in a rewritten environment.

    The report is asserted whole, because its format is shared with
    goselfupdate so tools built on either library report an update alike.
    """
    run_update(config(RecordingSource([], subjects=['feat: one'])))

    assert exits == [0]
    assert capsys.readouterr().out == '✓ demo updated: v1.0.0 → v2.0.0\n\nChanges:\n  • feat: one\n'


def test_skip_changelog_asks_the_source_for_nothing(pinned, installs, exits, events) -> None:
    run_update(config(RecordingSource(events)), skip_changelog=True)

    assert events == ['install', 'exit']


def test_already_at_latest_installs_nothing_and_returns(pinned, installs, exits) -> None:
    source = RecordingSource([], tag='v1.0.0')

    run_update(config(source))

    assert source.calls == 1
    assert not installs
    assert not exits


def test_check_only_reports_without_installing(pinned, installs, exits, capsys) -> None:
    run_update(config(RecordingSource([], subjects=['feat: one'])), check_only=True)

    assert not installs
    assert not exits
    output = capsys.readouterr().out
    assert 'demo update available: v1.0.0 → v2.0.0' in output
    assert '  • feat: one' in output


@pytest.mark.parametrize(
    ('requirement', 'check_only', 'reason'),
    [
        ('editable = "/src/demo"', False, 'update the checkout'),
        ('git = "https://github.com/x/demo.git"', True, 'installed from its default branch rather than a release tag'),
    ],
)
def test_an_install_update_refuses_is_refused_before_any_request(
    make_receipt, installs, exits, capsys, requirement: str, check_only: bool, reason: str
) -> None:
    """`--check` refuses what an update refuses, rather than comparing a version that names no release."""
    make_receipt('demo', f'{{ name = "demo", {requirement} }}')
    source = RecordingSource([])

    with pytest.raises(typer.Exit) as raised:
        run_update(config(source, version='2.0.0-post.3+1a2b3c4'), check_only=check_only)

    assert raised.value.exit_code == 1
    assert source.calls == 0
    assert not installs
    assert reason in capsys.readouterr().err


def test_an_install_failure_exits_non_zero(pinned, exits, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_: object, **__: object) -> None:
        raise InstallFailedError('uv tool install failed: no such ref')

    monkeypatch.setattr(update_module, 'run_install', explode)

    with pytest.raises(typer.Exit) as raised:
        run_update(config(RecordingSource([])))

    assert raised.value.exit_code == 1
    assert isinstance(raised.value.__cause__, InstallFailedError)
    assert not exits


def test_a_tag_with_no_lock_is_named_on_stderr(pinned, installs, exits, capsys, lock: StubLock) -> None:
    lock.pins = None
    unlocked = Result(current='v1.0.0', latest='v2.0.0', lock_missing=True)

    run_update(config(RecordingSource([])))

    assert capsys.readouterr().err == f'! demo: {unlocked.lock_warning}\n'
    assert exits == [0]


def test_a_locked_update_prints_no_warning(pinned, installs, exits, capsys) -> None:
    run_update(config(RecordingSource([])))

    assert capsys.readouterr().err == ''


def test_an_unreadable_lock_exits_non_zero_having_installed_nothing(pinned, installs, exits, lock: StubLock) -> None:
    lock.error = LockUnreadableError('could not clone https://github.com/x/demo.git at v2.0.0 to read its uv.lock: not found')

    with pytest.raises(typer.Exit) as raised:
        run_update(config(RecordingSource([])))

    assert raised.value.exit_code == 1
    assert raised.value.__cause__ is lock.error
    assert not installs
    assert not exits

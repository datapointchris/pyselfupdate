"""run_update().

The assertions that matter here are about ordering, not output: every step that
reaches the network or imports a module must happen before the install, and the
process must not return into its CLI afterwards. `run_install` and `exit_now`
are both patched -- one would rebuild a real virtual environment, the other
would take the test runner down with it.
"""

from __future__ import annotations

import pytest
import typer
from conftest import StubSource

from pyselfupdate import Config
from pyselfupdate import typercmd
from pyselfupdate import updater as update_module
from pyselfupdate.errors import InstallFailedError
from pyselfupdate.typercmd import run_update


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


@pytest.fixture
def installs(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> list[str]:
    recorded: list[str] = []

    def record(requirement: str, quiet: bool = True) -> None:
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
    """The failure this ordering exists for: syncer 4.0.0 fetched it after."""
    source = RecordingSource(events, subjects=['feat: one'])

    run_update(config(source))

    assert events == ['changelog', 'install', 'exit']


def test_an_applied_update_ends_the_process(pinned, installs, exits, capsys) -> None:
    """Returning would hand control back to typer, in a rewritten environment."""
    run_update(config(RecordingSource([], subjects=['feat: one'])))

    assert exits == [0]
    output = capsys.readouterr().out
    assert 'demo updated: v1.0.0 → v2.0.0' in output
    assert '  • feat: one' in output


def test_skip_changelog_asks_the_source_for_nothing(pinned, installs, exits, events) -> None:
    run_update(config(RecordingSource(events)), skip_changelog=True)

    assert events == ['install', 'exit']


def test_already_at_latest_installs_nothing_and_returns(pinned, installs, exits, capsys) -> None:
    run_update(config(RecordingSource([], tag='v1.0.0')))

    assert not installs
    assert not exits
    assert 'demo already at latest: v1.0.0' in capsys.readouterr().out


def test_check_only_reports_without_installing(pinned, installs, exits, capsys) -> None:
    run_update(config(RecordingSource([], subjects=['feat: one'])), check_only=True)

    assert not installs
    assert not exits
    output = capsys.readouterr().out
    assert 'demo update available: v1.0.0 → v2.0.0' in output
    assert '  • feat: one' in output


def test_a_local_install_is_refused_before_any_request(make_receipt, installs, exits, capsys) -> None:
    make_receipt('demo', '{ name = "demo", editable = "/Users/chris/tools/demo" }')
    source = RecordingSource([])

    with pytest.raises(typer.Exit) as raised:
        run_update(config(source))

    assert raised.value.exit_code == 1
    assert source.calls == 0
    assert not installs
    assert 'update the checkout' in capsys.readouterr().err


def test_an_install_failure_exits_non_zero(pinned, exits, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(requirement: str, quiet: bool = True) -> None:
        raise InstallFailedError('uv tool install failed: no such ref')

    monkeypatch.setattr(update_module, 'run_install', explode)

    with pytest.raises(typer.Exit) as raised:
        run_update(config(RecordingSource([])))

    assert raised.value.exit_code == 1
    assert not exits
    assert 'demo update failed: uv tool install failed: no such ref' in capsys.readouterr().err

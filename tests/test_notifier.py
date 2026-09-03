"""The gate, the interval, and the notice.

The gate is the part that decides whether anything happens at all, so most of
these assert that nothing happened -- and, crucially, that the source was never
consulted. A skip that still hits the network is a skip that failed.
"""

from __future__ import annotations

import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import FakeStream
from conftest import StubSource
from conftest import written_state

from pyselfupdate import Config
from pyselfupdate import notify
from pyselfupdate.errors import SourceError
from pyselfupdate.notifier import Skip
from pyselfupdate.notifier import _is_interactive
from pyselfupdate.notifier import _parse_interval
from pyselfupdate.notifier import enabled

# An interval of zero: the next call is always due. Named because a bare
# timedelta() at a call site reads as an oversight rather than the point.
IMMEDIATELY = timedelta()


@pytest.fixture
def pinned(make_receipt):
    make_receipt('demo', '{ name = "demo", git = "https://github.com/x/demo.git?rev=v1.0.0" }')


def config(source: StubSource) -> Config:
    return Config(tool='demo', owner='x', version='1.0.0', source=source)


def test_notifies_once_when_behind(pinned, state_home: Path, out) -> None:
    source = StubSource(tag='v2.0.0')

    outcome = notify(config(source), defer=False, interactive=True, out=out)

    assert outcome.checked
    assert outcome.update_available
    assert source.calls == 1
    assert 'demo v2.0.0 available (running v1.0.0)' in out.text
    assert 'run `demo update`' in out.text


def test_says_nothing_when_current(pinned, state_home: Path, out) -> None:
    outcome = notify(config(StubSource(tag='v1.0.0')), defer=False, interactive=True, out=out)

    assert outcome.checked
    assert not outcome.update_available
    assert out.lines == []


def test_does_not_notify_twice_inside_the_interval(pinned, state_home: Path, out) -> None:
    source = StubSource(tag='v2.0.0')

    first = notify(config(source), defer=False, interactive=True, out=out)
    second = notify(config(source), defer=False, interactive=True, out=out)

    assert first.checked
    assert second.skip is Skip.INTERVAL
    assert source.calls == 1, 'the second run must not reach the source'


def test_checks_again_once_the_interval_has_elapsed(pinned, state_home: Path, out) -> None:
    source = StubSource(tag='v2.0.0')

    notify(config(source), defer=False, interactive=True, out=out)
    notify(config(source), interval=IMMEDIATELY, defer=False, interactive=True, out=out)

    assert source.calls == 2


def test_the_timestamp_is_written_before_the_check_not_after(pinned, state_home: Path, out) -> None:
    """gh stamps only on success, so a rate-limited user retries on every run.

    An interval exists to bound the request rate; only stamping first does that.
    """
    source = StubSource(error=SourceError('rate limit exceeded'))

    outcome = notify(config(source), defer=False, interactive=True, out=out)

    assert outcome.skip is Skip.FAILED
    stored = written_state(state_home, 'demo')
    assert stored['checked_at_epoch'] > 0, 'a failed check must still consume its interval'
    assert stored['last_error'] == 'rate limit exceeded'

    # And the next run inside the window must not retry.
    assert notify(config(source), defer=False, interactive=True, out=out).skip is Skip.INTERVAL
    assert source.calls == 1


def test_a_failure_prints_nothing(pinned, state_home: Path, out) -> None:
    notify(config(StubSource(error=SourceError('boom'))), defer=False, interactive=True, out=out)
    assert out.lines == [], 'the notify path never prints an error'


def test_a_later_success_clears_the_recorded_error(pinned, state_home: Path, out) -> None:
    notify(config(StubSource(error=SourceError('boom'))), defer=False, interactive=True, out=out)
    notify(config(StubSource(tag='v2.0.0')), interval=IMMEDIATELY, defer=False, interactive=True, out=out)

    assert written_state(state_home, 'demo')['last_error'] == ''


@pytest.mark.parametrize('variable', ['NO_AUTO_UPDATE', 'DEMO_NO_AUTO_UPDATE'])
@pytest.mark.parametrize('value', ['', '0', 'false', '1'])
def test_the_kill_switch_is_presence_only(
    pinned, state_home: Path, out, monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    """Presence-only, any value, so NO_AUTO_UPDATE=0 cannot mean "on"."""
    monkeypatch.setenv(variable, value)
    source = StubSource(tag='v2.0.0')

    assert notify(config(source), defer=False, interactive=True, out=out).skip is Skip.DISABLED
    assert source.calls == 0


@pytest.mark.parametrize('variable', ['CI', 'BUILD_NUMBER', 'RUN_ID', 'GITHUB_ACTIONS', 'CODESPACES'])
def test_ci_is_never_notified(pinned, state_home: Path, out, monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    monkeypatch.setenv(variable, '1')
    source = StubSource(tag='v2.0.0')

    assert notify(config(source), defer=False, interactive=True, out=out).skip is Skip.CI
    assert source.calls == 0


def test_a_non_terminal_is_never_notified(pinned, state_home: Path, out) -> None:
    """Both streams must be terminals, so `demo list > file 2>&1` stays clean."""
    source = StubSource(tag='v2.0.0')

    assert notify(config(source), defer=False, interactive=False, out=out).skip is Skip.NOT_A_TTY
    assert source.calls == 0
    assert out.lines == []


@pytest.mark.parametrize(
    ('stdout_tty', 'stderr_tty', 'expected'),
    [
        (True, True, True),
        # `demo list | jq` -- stdout is a pipe, so nothing is printed even
        # though stderr would still reach the terminal.
        (False, True, False),
        # `demo list 2> file` -- the notice would land in the file.
        (True, False, False),
        (False, False, False),
    ],
)
def test_terminal_detection_requires_both_streams(stdout_tty: bool, stderr_tty: bool, expected: bool) -> None:
    streams = (FakeStream(tty=stdout_tty), FakeStream(tty=stderr_tty))
    assert _is_interactive(streams) is expected


def test_terminal_detection_survives_a_stream_without_isatty() -> None:
    class Bare:
        pass

    assert _is_interactive((Bare(),)) is False


def test_a_local_install_is_never_notified(make_receipt, state_home: Path, out) -> None:
    make_receipt('demo', '{ name = "demo", editable = "/Users/chris/tools/demo" }')
    source = StubSource(tag='v2.0.0')

    assert notify(config(source), defer=False, interactive=True, out=out).skip is Skip.LOCAL_INSTALL
    assert source.calls == 0


def test_a_branch_tracking_install_is_never_notified(make_receipt, state_home: Path, out) -> None:
    """Its version says nothing about how far behind the checkout is."""
    make_receipt('demo', '{ name = "demo", git = "https://github.com/x/demo.git" }')
    source = StubSource(tag='v2.0.0')

    assert notify(config(source), defer=False, interactive=True, out=out).skip is Skip.LOCAL_INSTALL
    assert source.calls == 0


def test_an_uninstalled_tool_is_never_notified(uv_tools: Path, state_home: Path, out) -> None:
    """Failing closed: if it cannot be identified, it is not nagged."""
    source = StubSource(tag='v2.0.0')

    assert notify(config(source), defer=False, interactive=True, out=out).skip is Skip.LOCAL_INSTALL
    assert source.calls == 0


def test_enabled_reports_the_reason_without_touching_the_network(pinned, monkeypatch: pytest.MonkeyPatch) -> None:
    source = StubSource(tag='v2.0.0')
    monkeypatch.setenv('CI', '1')

    allowed, reason = enabled(config(source).resolved(), interactive=True)

    assert not allowed
    assert reason is Skip.CI
    assert source.calls == 0


def test_state_carries_both_timestamp_forms(pinned, state_home: Path, out) -> None:
    """The epoch field is what lets the bash implementation do the same math."""
    notify(config(StubSource(tag='v2.0.0')), defer=False, interactive=True, out=out)

    stored = written_state(state_home, 'demo')
    assert stored['schema'] == 1
    assert stored['tool'] == 'demo'
    assert stored['checked_at'].endswith('Z')
    assert abs(stored['checked_at_epoch'] - time.time()) < 60
    assert stored['current_version'] == 'v1.0.0'
    assert stored['latest_version'] == 'v2.0.0'


def test_notify_never_raises_even_when_everything_is_wrong(state_home: Path, out) -> None:
    assert notify(Config(tool='')).skip is Skip.FAILED


@pytest.mark.parametrize(
    ('raw', 'seconds'),
    [('30s', 30), ('30m', 1800), ('24h', 86400), ('7d', 604800), ('90', 90), ('1.5h', 5400)],
)
def test_interval_parsing(raw: str, seconds: float) -> None:
    parsed = _parse_interval(raw)
    assert parsed is not None
    assert parsed.total_seconds() == seconds


@pytest.mark.parametrize('raw', ['', 'soon', '-1h', 'h', 'abc'])
def test_unparseable_intervals_are_ignored(raw: str) -> None:
    assert _parse_interval(raw) is None


def test_the_tool_interval_outranks_the_fleet_interval(pinned, state_home: Path, out, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('AUTO_UPDATE_INTERVAL', '99d')
    monkeypatch.setenv('DEMO_AUTO_UPDATE_INTERVAL', '0s')
    source = StubSource(tag='v2.0.0')

    notify(config(source), defer=False, interactive=True, out=out)
    notify(config(source), defer=False, interactive=True, out=out)

    assert source.calls == 2


@pytest.mark.parametrize('argv', [['--help'], ['-h'], ['list', '--help'], ['list', '-h']])
def test_a_help_screen_is_never_notified(pinned, state_home: Path, out, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """Click runs a group's callback before answering a subcommand's --help.

    Nothing a notice is for happens on a help screen, and the check costs a
    releases-API call and a state write every time one is printed.
    """
    monkeypatch.setattr(sys, 'argv', ['demo', *argv])
    source = StubSource(tag='v2.0.0')

    assert notify(config(source), defer=False, interactive=True, out=out).skip is Skip.HELP
    assert source.calls == 0
    assert out.lines == []


@pytest.mark.parametrize('argv', [['list'], [], ['run', '--', '--help'], ['--json']])
def test_ordinary_command_lines_are_still_notified(pinned, state_home: Path, out, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """Everything after `--` is the command's own argument, not a request for help."""
    monkeypatch.setattr(sys, 'argv', ['demo', *argv])
    source = StubSource(tag='v2.0.0')

    assert notify(config(source), defer=False, interactive=True, out=out).skip is not Skip.HELP
    assert source.calls == 1

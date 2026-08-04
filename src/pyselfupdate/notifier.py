"""Telling the user a newer release exists, and nothing else.

This layer never installs. `<tool> update` installs, and `<tool> update` is
where errors are printed; a failure here is recorded in the state file and
swallowed. That single rule is what keeps a dev checkout from printing an
update failure on every invocation.
"""

from __future__ import annotations

import atexit
import os
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Any
from typing import TextIO

from pyselfupdate import state as state_module
from pyselfupdate.config import Config
from pyselfupdate.errors import SelfUpdateError
from pyselfupdate.install import InstallKind
from pyselfupdate.install import read_installation
from pyselfupdate.updater import check

DEFAULT_INTERVAL = timedelta(hours=24)

# Environment variables. Presence-only, any value including empty -- the
# NO_COLOR convention -- so that NO_AUTO_UPDATE=0 cannot mean "on". The interval
# gets its own name precisely so presence and value never share one variable.
FLEET_DISABLE = 'NO_AUTO_UPDATE'
FLEET_INTERVAL = 'AUTO_UPDATE_INTERVAL'

CI_VARIABLES = ('CI', 'BUILD_NUMBER', 'RUN_ID', 'GITHUB_ACTIONS', 'CODESPACES')


class Skip(Enum):
    """Why a check did not happen."""

    DISABLED = 'disabled'
    LOCAL_INSTALL = 'local-install'
    NOT_A_TTY = 'not-a-tty'
    CI = 'ci'
    INTERVAL = 'interval'
    FAILED = 'failed'


@dataclass(frozen=True)
class Outcome:
    """What `notify` did. Returned for tests and dashboards, not for control flow."""

    checked: bool = False
    skip: Skip | None = None
    current: str = ''
    latest: str = ''

    @property
    def update_available(self) -> bool:
        return bool(self.latest) and self.latest != self.current


def notify(
    config: Config,
    *,
    interval: timedelta | None = None,
    out: TextIO | None = None,
    defer: bool = True,
    interactive: bool | None = None,
) -> Outcome:
    """Check at most once per interval and print one line when behind.

    Never raises. Call it from a CLI's root callback and ignore the return
    value; an update notice must not be able to break the command the user
    actually typed.

    With `defer` the notice is printed at interpreter exit rather than
    immediately, so it lands after the command's own output instead of being
    buried in it.

    `interactive` overrides terminal detection. Pass False from a program that
    already knows it is writing somewhere a human will not read -- into a pager,
    a log, a structured-output mode -- since nothing about the streams
    themselves reveals that.
    """
    try:
        return _notify(config, interval=interval, out=out, defer=defer, interactive=interactive)
    except SelfUpdateError:
        return Outcome(skip=Skip.FAILED)
    except Exception:  # noqa: BLE001 - a notice may never break the caller
        return Outcome(skip=Skip.FAILED)


def enabled(config: Config, *, interactive: bool | None = None) -> tuple[bool, Skip | None]:
    """Whether a check would run, without touching the network or the clock.

    Backs a fleet dashboard and a `<tool> update --why`. The interval is
    deliberately not consulted: this answers "is this tool opted in", not "is it
    due".
    """
    if _disabled_by_env(config.tool):
        return False, Skip.DISABLED
    if not (_is_interactive() if interactive is None else interactive):
        return False, Skip.NOT_A_TTY
    if _in_ci():
        return False, Skip.CI
    if _is_local_install(config.tool):
        return False, Skip.LOCAL_INSTALL
    return True, None


def _notify(
    config: Config,
    *,
    interval: timedelta | None,
    out: TextIO | None,
    defer: bool,
    interactive: bool | None,
) -> Outcome:
    resolved = config.resolved()
    tool = resolved.tool

    allowed, reason = enabled(resolved, interactive=interactive)
    if not allowed:
        return Outcome(skip=reason)

    stored = state_module.read(tool)
    window = _interval(tool, interval)
    if not _is_due(stored, window):
        return Outcome(skip=Skip.INTERVAL)

    # Stamped before the network call, not after. gh stamps only on success, so
    # a rate-limited or offline user re-hits the API on every single invocation
    # until the window resets. The whole point of an interval is to bound the
    # request rate, and only this ordering actually does that.
    state_module.write(state_module.stamp(stored))

    try:
        result = check(resolved)
    except SelfUpdateError as error:
        stored.last_error = str(error)
        state_module.write(stored)
        return Outcome(skip=Skip.FAILED)

    stored.last_error = ''
    stored.current_version = result.current
    stored.latest_version = result.latest
    state_module.write(stored)

    if not result.update_available:
        return Outcome(checked=True, current=result.current, latest=result.latest)

    message = f'{tool} {result.latest} available (running {result.current}) — run `{tool} update`'
    _emit(message, out=out, defer=defer)
    return Outcome(checked=True, current=result.current, latest=result.latest)


def _emit(message: str, *, out: TextIO | None, defer: bool) -> None:
    stream = out or sys.stderr
    if not defer:
        print(message, file=stream)
        return

    def flush() -> None:
        try:
            print(message, file=stream)
        except (OSError, ValueError):
            # The stream can be closed by the time interpreter shutdown runs.
            return

    atexit.register(flush)


def _disabled_by_env(tool: str) -> bool:
    return FLEET_DISABLE in os.environ or _tool_variable(tool, 'NO_AUTO_UPDATE') in os.environ


def _in_ci() -> bool:
    return any(name in os.environ for name in CI_VARIABLES)


def _is_interactive(streams: Iterable[Any] | None = None) -> bool:
    """Both streams must be terminals.

    Checking both matters: `tool list | jq` should still be allowed to notify on
    stderr, but `tool list > file 2>&1` must not, or the notice lands in the
    file. Requiring both is the conservative reading and matches gh.

    Typed loosely rather than as TextIO: all this needs is `isatty`, the check
    below is deliberate duck-typing, and demanding the full TextIO surface
    would reject both a reasonable stand-in and the object-without-isatty
    case this is written to survive.

    `streams` exists so this can be tested without reassigning `sys.stdout`,
    which pytest's capture reinstalls between fixture setup and the test call.
    """
    for stream in streams or (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                return False
        except (AttributeError, ValueError):
            return False
    return True


def _is_local_install(tool: str) -> bool:
    """A local, editable, or branch-tracking install must never be nagged.

    Failing closed: anything that cannot be positively identified as a pinned
    release install is treated as local, because a false negative here prints a
    wrong notice on every command in a dev checkout.
    """
    try:
        installation = read_installation(tool)
    except SelfUpdateError:
        return True
    if installation.kind is InstallKind.LOCAL:
        return True
    return installation.kind is InstallKind.GIT and not installation.revision


def _interval(tool: str, override: timedelta | None) -> timedelta:
    if override is not None:
        return override
    for name in (_tool_variable(tool, 'AUTO_UPDATE_INTERVAL'), FLEET_INTERVAL):
        raw = os.environ.get(name)
        if raw:
            parsed = _parse_interval(raw)
            if parsed is not None:
                return parsed
    return DEFAULT_INTERVAL


def _parse_interval(raw: str) -> timedelta | None:
    """Parse `30m`, `24h`, `7d`, or a bare number of seconds."""
    raw = raw.strip().lower()
    if not raw:
        return None
    units = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    multiplier = units.get(raw[-1])
    number = raw[:-1] if multiplier else raw
    try:
        value = float(number)
    except ValueError:
        return None
    if value < 0:
        return None
    return timedelta(seconds=value * (multiplier or 1))


def _is_due(stored: state_module.State, window: timedelta) -> bool:
    if not stored.checked_at_epoch:
        return True
    return time.time() - stored.checked_at_epoch >= window.total_seconds()


def _tool_variable(tool: str, suffix: str) -> str:
    return f'{tool.upper().replace("-", "_")}_{suffix}'

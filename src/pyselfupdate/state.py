"""The per-tool, per-machine state file.

One schema, shared byte-for-byte with goselfupdate and bashselfupdate, so that
any tool can read any other tool's state and a single dashboard can glob
`~/.local/state/*/autoupdate-*.json` with no per-tool knowledge.

State, not config and not cache: it persists across runs, it is not authored by
the user, and deleting it changes behavior rather than merely costing a
recompute. That is `XDG_STATE_HOME` by the Base Directory specification, and it
is where `gh` puts the same thing.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path

SCHEMA = 1

#: Names the file when the host cannot be read, so a filename is always
#: well-formed and two unidentifiable boxes collide only with each other.
UNKNOWN_MACHINE = 'unknown'


def machine() -> str:
    """The box this process runs on: bare hostname, no domain, lowercased.

    goselfupdate and bashselfupdate derive it the same way, so the three write
    interleaved files a single reader can enumerate.
    """
    try:
        return canonical_machine(socket.gethostname())
    except OSError:
        return UNKNOWN_MACHINE


def canonical_machine(name: str) -> str:
    """A hostname from any source, reduced to the form `machine` records.

    A hostname reaches a reader fully qualified as often as bare, so a
    comparison against a recorded name has to canonicalize both sides or it
    fails on every host.
    """
    return name.strip().lower().split('.')[0] or UNKNOWN_MACHINE


def filename(machine_name: str) -> str:
    """The file one machine writes inside a tool's state directory.

    The machine is part of the name because a state directory is a synced
    directory on some installations, and a file syncer has no merge: two boxes
    writing one path leaves one winner plus a conflict copy nobody reads. Both
    fields this file carries — the version installed here and the instant this
    box last checked — describe one machine, so the split costs nothing and
    makes the collision unreachable.
    """
    return f'autoupdate-{machine_name}.json'


@dataclass
class State:
    """One tool's record of its last update check."""

    tool: str
    schema: int = SCHEMA
    checked_at: str = ''

    # The same instant as `checked_at`, as an integer. Redundant on purpose:
    # BSD `date` cannot parse ISO-8601 without `-j -f` gymnastics, and the bash
    # implementation has to do interval arithmetic with `jq` and `date +%s`
    # alone. One duplicated field buys a portable bash implementation.
    checked_at_epoch: int = 0

    current_version: str = ''
    latest_version: str = ''
    # Non-empty when the last check failed. There is deliberately no separate
    # "skip reason" field: a gate that declines to check does not write this
    # file at all, which is what makes "no state file" observable proof that the
    # network was never touched.
    last_error: str = ''


def state_home() -> Path:
    override = os.environ.get('XDG_STATE_HOME')
    base = Path(override).expanduser() if override else Path.home() / '.local' / 'state'
    return base


def state_path(tool: str) -> Path:
    """Where this machine's state for a tool lives."""
    return state_home() / tool / filename(machine())


def read(tool: str) -> State:
    """A tool's state, or an empty one when it has never been written.

    A corrupt or unreadable file reads as empty rather than raising: the file is
    a throttle, and failing a user's command because the throttle cannot be read
    would be worse than checking one extra time.
    """
    path = state_path(tool)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, ValueError):
        return State(tool=tool)

    if not isinstance(payload, dict):
        return State(tool=tool)

    known = {field for field in State.__dataclass_fields__}
    return State(**{key: value for key, value in payload.items() if key in known} | {'tool': tool})


def write(state: State) -> None:
    """Persist a tool's state atomically.

    Written to a temporary file in the same directory and renamed, so a reader
    sees either the whole previous file or the whole new one. Failures are
    swallowed for the same reason `read` tolerates corruption.
    """
    path = state_path(state.tool)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f'.{path.name}.tmp')
        temporary.write_text(json.dumps(asdict(state), indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)
    except OSError:
        return


def stamp(state: State, moment: datetime | None = None) -> State:
    """Set both timestamp fields to the same instant."""
    now = moment or datetime.now(UTC)
    state.checked_at = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    state.checked_at_epoch = int(now.timestamp())
    return state

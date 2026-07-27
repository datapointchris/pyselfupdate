"""Reading and rewriting a uv tool installation.

This is the part with no analogue in goselfupdate. A Go tool updates by
replacing one file; a uv tool updates by rebuilding the virtual environment its
own interpreter is running inside, which is why `update` must be the last thing
a process does before it exits or re-execs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path

from pyselfupdate.errors import InstallFailedError
from pyselfupdate.errors import NotInstalledError

RECEIPT_NAME = 'uv-receipt.toml'


class InstallKind(Enum):
    """How a uv tool was installed, which decides whether it may be updated."""

    GIT = 'git'
    INDEX = 'index'
    LOCAL = 'local'


@dataclass(frozen=True)
class Installation:
    """What uv's own receipt says about an installed tool."""

    tool: str
    kind: InstallKind
    url: str = ''

    # The requested revision, empty when the install tracks the default branch.
    # An empty value on a GIT install is the interesting case: the tool was
    # installed from a moving target, so "up to date" has no meaning.
    revision: str = ''

    def is_updatable(self) -> bool:
        return self.kind is not InstallKind.LOCAL


def tool_dir() -> Path:
    """uv's tool directory.

    Resolved from the environment rather than by running `uv tool dir`, which
    costs a subprocess on a path that runs before every command.
    """
    override = os.environ.get('UV_TOOL_DIR')
    if override:
        return Path(override).expanduser()
    data_home = os.environ.get('XDG_DATA_HOME')
    base = Path(data_home).expanduser() if data_home else Path.home() / '.local' / 'share'
    return base / 'uv' / 'tools'


def read_installation(tool: str) -> Installation:
    """Parse uv's receipt for a tool.

    The receipt is uv's own record of how it installed something, written at
    install time. Reading it beats inferring the same thing at runtime from the
    executable's path, which cannot tell "uv put it there" apart from "someone
    dropped a binary in the same directory".
    """
    receipt = tool_dir() / tool / RECEIPT_NAME
    if not receipt.is_file():
        raise NotInstalledError(f'{tool} is not installed as a uv tool ({receipt} does not exist)')

    try:
        payload = tomllib.loads(receipt.read_text(encoding='utf-8'))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise NotInstalledError(f'cannot read {receipt}: {error}') from error

    requirements = (payload.get('tool') or {}).get('requirements') or []
    for requirement in requirements:
        if requirement.get('name') != tool:
            continue

        # A local checkout, however uv spelled it. Reinstalling one would
        # discard the working copy the user is developing against.
        for key in ('directory', 'path', 'editable'):
            if requirement.get(key):
                return Installation(tool, InstallKind.LOCAL, url=str(requirement[key]))

        git = requirement.get('git')
        if git:
            url, _, query = str(git).partition('?')
            return Installation(tool, InstallKind.GIT, url=url, revision=_revision(query))

        return Installation(tool, InstallKind.INDEX)

    raise NotInstalledError(f'{receipt} lists no requirement named {tool}')


def _revision(query: str) -> str:
    """The `rev=` from a git requirement's query string.

    uv writes `...git?rev=v1.2.3` for a pinned install and omits the query
    entirely for one that follows the default branch.
    """
    for part in query.split('&'):
        key, _, value = part.partition('=')
        if key == 'rev' and value:
            return value
    return ''


def current_version(package: str) -> str:
    """The running build's version, or an empty string when unknown."""
    try:
        return installed_version(package)
    except PackageNotFoundError:
        return ''


def requirement_for(installation: Installation, ref: str) -> str:
    """The requirement string that installs `ref` of an already-installed tool."""
    if installation.kind is InstallKind.GIT:
        return f'{installation.tool} @ git+{installation.url}@{ref}'
    return f'{installation.tool}=={ref.removeprefix("v")}'


def run_install(requirement: str, *, quiet: bool = True) -> None:
    """Install a requirement over the existing tool.

    `--force` is what allows an entry point that already exists to be replaced;
    without it uv refuses rather than overwriting.
    """
    executable = shutil.which('uv')
    if not executable:
        raise InstallFailedError('uv is not on PATH, so the tool cannot reinstall itself')

    command = [executable, 'tool', 'install', '--force', requirement]
    if quiet:
        command.insert(1, '--quiet')

    completed = subprocess.run(  # noqa: S603 - argv is built here, never a shell string
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or '').strip().splitlines()
        message = detail[-1] if detail else f'exit status {completed.returncode}'
        raise InstallFailedError(f'uv tool install failed: {message}')


def reexec() -> None:
    """Replace this process with the newly installed one.

    `uv tool install --force` rewrites the virtual environment this interpreter
    is running inside. Unlike a binary rename -- where the process holds an
    inode and is untouched -- that pulls modules out from under a live process,
    so anything imported afterwards may fail in ways that are very hard to read.
    Re-exec immediately, with everything already imported.

    Never returns.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    # Re-exec is the operation, so B606 cannot be designed away. argv[0] is this
    # program and argv is passed through unchanged -- no shell, no interpolation.
    # subprocess plus sys.exit would satisfy the linter and be strictly worse:
    # an extra process, signal forwarding to hand-roll, and both images resident.
    os.execv(sys.argv[0], sys.argv)  # noqa: S606  # nosec B606

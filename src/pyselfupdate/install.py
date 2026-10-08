"""Reading and rewriting a uv tool installation.

This is the part with no analog in goselfupdate. A Go tool updates by
replacing one file; a uv tool updates by rebuilding the virtual environment its
own interpreter is running inside, which is why `update` must be the last thing
a process does before it exits or re-execs.

`uv tool install` never reads a lock. Handed `<tool> @ git+<url>@<tag>`, it
resolves every dependency afresh, so the tool would run on whatever was newest
that day while its CI tested the lock. A git install is therefore held to the
`uv.lock` at the tag being installed: `read_lock` exports it, and `run_install`
hands the result to uv. uv records both lists in the receipt, which is how
anything reading the receipt afterwards tells a locked install from one that
is not.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path

from pyselfupdate.errors import InstallFailedError
from pyselfupdate.errors import LockUnreadableError
from pyselfupdate.errors import NotInstalledError

RECEIPT_NAME = 'uv-receipt.toml'

LOCK_NAME = 'uv.lock'

EXPORT = ('export', '--frozen', '--no-default-groups', '--no-emit-workspace', '--no-hashes', '--no-header', '--no-annotate')
"""The runtime closure as the lock records it, without the project's own packages.

`--no-emit-workspace` leaves out the tool itself and any workspace member, which
the commit being installed already pins. `--no-hashes` because uv does not check
the hashes a constraints file carries, and keeping them would imply a
verification nothing performs.
"""


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


@dataclass(frozen=True)
class Pins:
    """What a `uv.lock` pins, split into the two forms `uv tool install` takes.

    A registry pin is a constraint, which holds a package to the locked version
    without adding it. A URL pin has to be an override: uv refuses a constraint
    whose URL differs from the one the package declares, and a lock records
    `git+<repo>@<commit>` where the package declared `git+<repo>`.
    """

    constraints: tuple[str, ...] = ()
    overrides: tuple[str, ...] = ()

    def arguments(self, directory: Path) -> list[str]:
        """Writes each non-empty list into `directory` and returns the flags naming them.

        An empty file is left out rather than passed, because uv warns on one.
        """
        flags: list[str] = []
        for flag, lines in (('--constraints', self.constraints), ('--overrides', self.overrides)):
            if not lines:
                continue
            path = directory / f'{flag.removeprefix("--")}.txt'
            path.write_text(''.join(f'{line}\n' for line in lines), encoding='utf-8')
            flags += [flag, str(path)]
        return flags


def read_lock(url: str, ref: str) -> Pins | None:
    """What the `uv.lock` at `ref` of a git repository pins, or None when it has none.

    Reads a shallow clone of the one ref, then lets `uv export` interpret the
    lock rather than parsing a format uv owns.

    Raises `LockUnreadableError` when the ref will not clone or uv will not
    export its lock. A tag with no lock at all is not an error; the caller
    installs it unlocked and says so.
    """
    git = shutil.which('git')
    uv = shutil.which('uv')
    if not git or not uv:
        missing = 'git' if not git else 'uv'
        raise LockUnreadableError(f'{missing} is not on PATH, so the uv.lock at {ref} cannot be read')

    # A directory left behind in the system temp is not worth failing an update over.
    with tempfile.TemporaryDirectory(prefix='pyselfupdate-lock-', ignore_cleanup_errors=True) as scratch:
        checkout = Path(scratch) / 'checkout'
        cloned = _run([git, 'clone', '--quiet', '--depth', '1', '--branch', ref, url, str(checkout)])
        if cloned.returncode != 0:
            raise LockUnreadableError(f'could not clone {url} at {ref} to read its uv.lock: {_reason(cloned)}')
        if not (checkout / LOCK_NAME).is_file():
            return None
        exported = _run([uv, *EXPORT], cwd=checkout)
        if exported.returncode != 0:
            raise LockUnreadableError(f'uv export would not read the uv.lock at {ref}: {_reason(exported)}')
    return _pins_from_export(exported.stdout)


def _pins_from_export(exported: str) -> Pins:
    """A path requirement is neither kind, and is dropped: it is inside the commit being installed."""
    constraints: list[str] = []
    overrides: list[str] = []
    for line in exported.splitlines():
        line = line.strip()
        requirement = line.split(';', 1)[0]
        if ' @ ' in requirement:
            overrides.append(line)
        elif '==' in requirement:
            constraints.append(line)
    return Pins(tuple(constraints), tuple(overrides))


def run_install(requirement: str, *, quiet: bool = True, pins: Pins | None = None) -> None:
    """Install a requirement over the existing tool, held to `pins` when given.

    `--force` is what allows an entry point that already exists to be replaced;
    without it uv refuses rather than overwriting.
    """
    executable = shutil.which('uv')
    if not executable:
        raise InstallFailedError('uv is not on PATH, so the tool cannot reinstall itself')

    with tempfile.TemporaryDirectory(prefix='pyselfupdate-pins-', ignore_cleanup_errors=True) as scratch:
        held = pins.arguments(Path(scratch)) if pins else []
        command = [executable, 'tool', 'install', '--force', *held, requirement]
        if quiet:
            command.insert(1, '--quiet')
        completed = _run(command)

    if completed.returncode != 0:
        raise InstallFailedError(f'uv tool install failed: {_reason(completed)}')


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def _reason(completed: subprocess.CompletedProcess[str]) -> str:
    """Everything a failing command printed.

    No one line carries the reason. uv puts a lock's schema error on its first
    line, git puts a missing ref on its last, and uv wraps a resolution failure
    so its last line is half a sentence.
    """
    return (completed.stderr or completed.stdout or '').strip() or f'exit status {completed.returncode}'


def exit_now(code: int = 0) -> None:
    """End this process immediately, after flushing what it has written.

    The sibling of `reexec` for when there is nothing left to run. Both exist
    for the same reason: the environment has been rewritten, so no further
    import can be trusted. `sys.exit` raises SystemExit, which unwinds through
    whatever CLI framework called us and then through interpreter shutdown --
    and both are free to import a module they had not needed yet, from a
    directory that is no longer the one this process started in. `os._exit`
    skips both, which is why the flushes are done here rather than left to the
    shutdown that no longer happens.

    Never returns.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


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

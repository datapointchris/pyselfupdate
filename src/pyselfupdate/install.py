"""Reading and rewriting a uv tool installation.

This is the part with no analog in goselfupdate. A Go tool updates by
replacing one file; a uv tool updates by rebuilding the virtual environment its
own interpreter is running inside, which is why `update` must be the last thing
a process does before it exits or re-execs.

`uv tool install` never reads a lock. Handed `<tool> @ git+<url>@<tag>`, it
resolves every dependency to its newest version, while the tool's CI tested the
versions in its `uv.lock`. So a git install is held to the lock at the tag being
installed: `read_lock` turns it into constraints and overrides, and
`run_install` passes them to uv. uv records both in the receipt, so a later
`uv tool upgrade` stays held to them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from itertools import chain
from pathlib import Path

from pyselfupdate.errors import IndexUnreadableError
from pyselfupdate.errors import InstallFailedError
from pyselfupdate.errors import LockUnreadableError
from pyselfupdate.errors import NotInstalledError

RECEIPT_NAME = 'uv-receipt.toml'

LOCK_NAME = 'uv.lock'

EXPORT = ('export', '--frozen', '--no-default-groups', '--no-emit-workspace', '--no-hashes', '--no-header', '--no-annotate')
"""The lock's runtime dependencies, one requirement per line.

`--no-emit-workspace` leaves out the tool itself and any workspace member, which
the commit being installed already pins. `--no-hashes` keeps each requirement
on one line: uv writes every hash as a continuation line, and
`_pins_from_export` reads line by line.
"""

LOCK_READ_TIMEOUT_SECONDS = 120.0
"""The bound on each of the clone and the export that read a release's lock.

Both run before anything is installed, so a stalled remote or a credential
prompt nobody answers fails the update with `LockUnreadableError` rather than
holding the command someone typed.
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

    # Whatever followed the `@` at install time, read from the receipt by
    # `_split_git`. `updater.installed_from_release` reads it as a release tag
    # or refuses it.
    revision: str = ''

    extras: tuple[str, ...] = ()

    # The receipt's other requirements, from `--with` and `--with-editable`, as
    # requirements-file lines, because a `-e` line is the only way to pass an
    # editable back.
    with_requirements: tuple[str, ...] = ()

    # uv flags reproducing the receipt's Python and package sources, from
    # `_source_flags`. `uv tool install --force` reads neither back from the
    # receipt it replaces, so an update passes them itself.
    source_flags: tuple[str, ...] = ()

    # Names of the receipt requirements `_requirement_line` returns None for.
    # `read_installation` records them rather than raising: the notify gate
    # treats a failed read as a local install and prints no notice. `update`
    # raises NotInstalledError on them instead of dropping them.
    unrebuildable: tuple[str, ...] = field(default=(), repr=False)


def tool_dir() -> Path:
    """uv's tool directory, in the order uv resolves it.

    `UV_TOOL_DIR` first. Otherwise uv's data directory: `%APPDATA%\\uv` on
    Windows, where uv ignores `XDG_DATA_HOME`, and `$XDG_DATA_HOME/uv` or
    `~/.local/share/uv` everywhere else.

    Resolved from the environment rather than by running `uv tool dir`, which
    costs a subprocess on a path that runs before every command.
    """
    override = os.environ.get('UV_TOOL_DIR')
    if override:
        return Path(override).expanduser()
    if sys.platform == 'win32':
        appdata = os.environ.get('APPDATA')
        roaming = Path(appdata) if appdata else Path.home() / 'AppData' / 'Roaming'
        return roaming / 'uv' / 'tools'
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
        raise NotInstalledError(
            f'{tool} is not installed as a uv tool ({receipt} does not exist). '
            f"Install a release with `uv tool install '{tool} @ git+<repository url>@<release tag>'`, "
            f'or `uv tool install {tool}` from a package index'
        )

    try:
        payload = tomllib.loads(receipt.read_text(encoding='utf-8'))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise NotInstalledError(f'cannot read {receipt}: {error}') from error

    recorded = payload.get('tool') or {}
    requirements = recorded.get('requirements') or []
    own = next((requirement for requirement in requirements if requirement.get('name') == tool), None)
    if own is None:
        raise NotInstalledError(f'{receipt} lists no requirement named {tool}')

    rebuilt: list[str] = []
    unrebuildable: list[str] = []
    for requirement in requirements:
        if requirement is own:
            continue
        line = _requirement_line(requirement)
        if line is None:
            unrebuildable.append(str(requirement.get('name') or requirement))
        else:
            rebuilt.append(line)

    def installed_as(kind: InstallKind, url: str = '', revision: str = '') -> Installation:
        extras = tuple(own.get('extras') or ())
        return Installation(tool, kind, url, revision, extras, tuple(rebuilt), _source_flags(recorded), tuple(unrebuildable))

    # A local checkout, however uv spelled it. Reinstalling one would
    # discard the working copy the user is developing against.
    for key in ('directory', 'path', 'editable'):
        if own.get(key):
            return installed_as(InstallKind.LOCAL, url=str(own[key]))

    git = own.get('git')
    if git:
        url, query, _ = _split_git(str(git))
        return installed_as(InstallKind.GIT, url=url, revision=query.get('rev', ''))

    return installed_as(InstallKind.INDEX)


def _source_flags(recorded: dict) -> tuple[str, ...]:
    """The uv flags that reinstall onto the receipt's Python from the receipt's package sources.

    Without them a reinstall resolves against PyPI alone and moves to uv's
    default Python. uv records a setting from a flag, the environment or a
    config file alike, and cannot say which. An `explicit` or `flat` index only
    a config file can declare, and that file is read again by the reinstall, so
    it is left out. Dropping any other recorded option changes how the tool
    resolves or builds, never whether its packages can be found.
    """
    options = recorded.get('options') or {}
    pairs: list[tuple[str, str]] = []
    if recorded.get('python'):
        pairs.append(('--python', str(recorded['python'])))
    for index in options.get('index') or ():
        if index.get('explicit') or index.get('format', 'simple') != 'simple' or not index.get('url'):
            continue
        if index.get('default'):
            pairs.append(('--default-index', str(index['url'])))
        else:
            name = index.get('name')
            pairs.append(('--index', f'{name}={index["url"]}' if name else str(index['url'])))
    if options.get('index-url'):
        pairs.append(('--index-url', str(options['index-url'])))
    pairs += [('--extra-index-url', str(url)) for url in options.get('extra-index-url') or ()]
    pairs += [('--find-links', str(location)) for location in options.get('find-links') or ()]
    pairs += [(f'--{key}', str(options[key])) for key in ('index-strategy', 'keyring-provider') if options.get(key)]
    if options.get('no-index'):
        pairs.append(('--no-index', ''))

    # A source declared in a config file is recorded again beside the flag that
    # passes it back. Passing each source once holds it at two copies, rather
    # than adding one to the receipt per update.
    distinct = dict.fromkeys(pairs)
    return tuple(part for flag, value in distinct for part in (flag, value) if part)


def _split_git(git: str) -> tuple[str, dict[str, str], str]:
    """A receipt's git source as its URL, its query and the commit after `#`.

    uv writes `...git?rev=v1.2.3` for `@v1.2.3`, `...git?rev=main` for
    `@main`, and omits the query entirely for an install that follows the
    default branch.
    """
    rest, _, commit = git.partition('#')
    url, _, query = rest.partition('?')
    pairs = (part.partition('=') for part in query.split('&') if part)
    return url, {key: value for key, _, value in pairs if value}, commit


_LINE_KEYS = frozenset({'name', 'extras', 'marker', 'specifier', 'git', 'subdirectory', 'url', 'path', 'directory', 'editable'})


def _requirement_line(requirement: dict) -> str | None:
    """A receipt requirement as a requirements-file line, or None when no line reproduces it.

    None covers a key outside `_LINE_KEYS`, an editable carrying extras or a
    marker, and a relative path.
    """
    name = requirement.get('name')
    if not name or set(requirement) - _LINE_KEYS:
        return None
    extras = requirement.get('extras') or ()
    named = f'{name}[{",".join(extras)}]' if extras else name
    marker = f' ; {requirement["marker"]}' if requirement.get('marker') else ''

    if requirement.get('editable'):
        uri = _file_uri(requirement['editable'])
        return f'-e {uri}' if uri and not extras and not marker else None
    if requirement.get('git'):
        url, query, commit = _split_git(str(requirement['git']))
        ref = query.get('rev') or query.get('tag') or query.get('branch') or commit
        subdirectory = query.get('subdirectory') or requirement.get('subdirectory')
        return f'{named} @ git+{url}{f"@{ref}" if ref else ""}{f"#subdirectory={subdirectory}" if subdirectory else ""}{marker}'
    if requirement.get('url'):
        subdirectory = requirement.get('subdirectory')
        return f'{named} @ {requirement["url"]}{f"#subdirectory={subdirectory}" if subdirectory else ""}{marker}'
    for key in ('directory', 'path'):
        if requirement.get(key):
            uri = _file_uri(requirement[key])
            return f'{named} @ {uri}{marker}' if uri else None
    return f'{named}{requirement.get("specifier") or ""}{marker}'


def _file_uri(path: str) -> str:
    """A `file://` URI for an absolute path, or an empty string for a relative one.

    The receipt does not record what a relative path is relative to.
    """
    candidate = Path(path)
    return candidate.as_uri() if candidate.is_absolute() else ''


def current_version(package: str) -> str:
    """The running build's version, or an empty string when unknown."""
    try:
        return installed_version(package)
    except PackageNotFoundError:
        return ''


INDEX_READ = ('pip', 'compile', '-', '--no-deps', '--no-header', '--no-annotate', '--quiet')
"""Resolves the requirement read from stdin, and only it, printing `<name>==<version>`."""


def read_index_latest(installation: Installation, *, timeout: float, allow_prerelease: bool = False) -> str:
    """The newest version of an index-installed tool that its package index serves, as uv prints it.

    A release can exist before its upload to the index finishes, and stays when
    the upload fails, so the release source cannot say what an index install
    can take. uv resolves the tool's name alone, with the receipt's package
    sources, and reads the index through the configuration and credentials the
    reinstall uses. The receipt's `--python` is passed back too, so a release
    that has dropped that Python is not offered. With none recorded, uv
    resolves for the running interpreter.

    The read is bounded by `timeout`, since the notify gate runs it before the
    command someone typed returns. Raises `IndexUnreadableError` when uv is not
    on PATH, outlasts the bound, or names no version.
    """
    uv = shutil.which('uv')
    if not uv:
        raise IndexUnreadableError(
            f'uv is not on PATH, so the versions of {installation.tool} its package index serves cannot be read. '
            'Add the directory holding uv to PATH, then check again'
        )
    python = () if '--python' in installation.source_flags else ('--python', sys.executable)
    prerelease = ('--prerelease', 'allow') if allow_prerelease else ()
    command = [uv, *INDEX_READ, *installation.source_flags, *python, *prerelease]
    try:
        resolved = run_command(command, stdin=f'{installation.tool}\n', timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise IndexUnreadableError(
            f'reading which version of {installation.tool} its package index serves took longer than {error.timeout:g}s'
        ) from error
    if resolved.returncode != 0:
        raise IndexUnreadableError(f'uv could not read which version of {installation.tool} its package index serves: {_reason(resolved)}')
    pinned = [line.split(';', 1)[0].partition('==')[2].strip() for line in resolved.stdout.splitlines() if '==' in line]
    if len(pinned) != 1 or not pinned[0]:
        raise IndexUnreadableError(
            f'uv resolved {installation.tool} from its package index to {resolved.stdout.strip()!r}, which names no one version'
        )
    return pinned[0]


def requirement_for(installation: Installation, ref: str) -> str:
    """The requirement string that installs `ref` of an already-installed tool, with the extras it has.

    An index install takes `ref` as a floor. uv records the specifier in the
    receipt, and `uv tool upgrade` refuses to move an exact pin.
    """
    named = f'{installation.tool}[{",".join(installation.extras)}]' if installation.extras else installation.tool
    if installation.kind is InstallKind.GIT:
        return f'{named} @ git+{installation.url}@{ref}'
    return f'{named}>={ref.removeprefix("v")}'


@dataclass(frozen=True)
class Pins:
    """What a `uv.lock` pins, as `--constraints` lines and `--overrides` lines.

    A registry pin is a constraint, which holds a package to the locked version
    without adding it. A URL pin has to be an override. A lock records
    `git+<repo>@<commit>` where the package declared `git+<repo>`, and uv
    refuses a constraint whose URL differs from the declared one with
    `Requirements contain conflicting URLs`.
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


def read_lock(url: str, ref: str, *, extras: Sequence[str] = ()) -> Pins | None:
    """What the `uv.lock` at `ref` of a git repository pins, or None when it has none.

    `extras` are the extras the tool is installed with. Each is passed to
    `uv export` as `--extra`, since a default export leaves an extra's
    dependencies out. `--all-extras` fails on a project that declares two
    extras as conflicting.

    Reads a shallow clone of the one ref and lets `uv export` interpret the lock.
    The lock is also read directly for one thing the export drops: the extras
    a dependent requests of a git dependency, which `_override_extras` restores.

    Raises `LockUnreadableError` when git or uv is not on PATH, the ref will not
    clone, uv will not export its lock, or either outlasts
    `LOCK_READ_TIMEOUT_SECONDS`. None is not a failure:
    `install_release` installs that tag unlocked and sets `lock_missing`.
    """
    git = shutil.which('git')
    uv = shutil.which('uv')
    if not git or not uv:
        missing = 'git' if not git else 'uv'
        raise LockUnreadableError(
            f'{missing} is not on PATH, so the uv.lock at {ref} cannot be read. '
            f'Install {missing}, or add the directory holding it to PATH, then run the update again'
        )

    # A clone that cannot be deleted stays in the system temp, and the update goes ahead.
    with tempfile.TemporaryDirectory(prefix='pyselfupdate-lock-', ignore_cleanup_errors=True) as scratch:
        checkout = Path(scratch) / 'checkout'
        clone = [git, 'clone', '--quiet', '--depth', '1', '--branch', ref, url, str(checkout)]
        try:
            cloned = run_command(clone, timeout=LOCK_READ_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            raise LockUnreadableError(f'cloning {url} at {ref} to read its uv.lock took longer than {error.timeout:g}s') from error
        if cloned.returncode != 0:
            raise LockUnreadableError(f'could not clone {url} at {ref} to read its uv.lock: {_reason(cloned)}')
        lock = checkout / LOCK_NAME
        if not lock.is_file():
            return None
        export = [uv, *EXPORT, *chain.from_iterable(('--extra', extra) for extra in extras)]
        try:
            exported = run_command(export, cwd=checkout, timeout=LOCK_READ_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            raise LockUnreadableError(f'uv export of the uv.lock at {ref} took longer than {error.timeout:g}s') from error
        if exported.returncode != 0:
            raise LockUnreadableError(f'uv export would not read the uv.lock at {ref}: {_reason(exported)}')
        try:
            requested = _override_extras(tomllib.loads(lock.read_text(encoding='utf-8')), extras)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise LockUnreadableError(f'cannot read the uv.lock at {ref}: {error}') from error
    return _pins_from_export(exported.stdout, requested)


def _override_extras(lock: dict, extras: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """The extras requested of each package, walking dependency edges from the project at the lock's root.

    An override replaces a requirement whole, extras included, and `uv export`
    writes `gitdep @ git+...` where the tool declared `gitdep[x] @ git+...`.
    Without this, an update leaves out the packages `x` pulls in. The tool then
    raises ModuleNotFoundError on its first import of one, after its old
    environment is gone. The lock keeps the extras on the edge that requests
    them: `{ name = "gitdep", extra = ["x"] }`.

    Only requested extras are followed, so an override never names an extra
    the installed tool did not ask for.
    """
    packages: dict[str, list[dict]] = {}
    for package in lock.get('package') or []:
        packages.setdefault(package.get('name', ''), []).append(package)
    project = ({'editable': '.'}, {'virtual': '.'})
    roots: list[str] = []
    for name, entries in packages.items():
        roots.extend(name for package in entries if package.get('source') in project)

    active: dict[str, set[str]] = {root: set(extras) for root in roots}
    pending = list(roots)
    while pending:
        name = pending.pop()
        for package in packages.get(name, ()):
            groups = package.get('optional-dependencies') or {}
            optional = chain.from_iterable(groups.get(extra, ()) for extra in sorted(active[name]))
            edges = [*(package.get('dependencies') or ()), *optional]
            for edge in edges:
                target, wanted = edge.get('name', ''), set(edge.get('extra') or ())
                if target not in active or not wanted <= active[target]:
                    active.setdefault(target, set()).update(wanted)
                    pending.append(target)
    return {name: tuple(sorted(wanted)) for name, wanted in active.items() if wanted}


def _pins_from_export(exported: str, extras: dict[str, tuple[str, ...]]) -> Pins:
    """Sorts `uv export` lines: ` @ ` before any marker makes an override, `==` a constraint.

    Anything else is a path requirement and is dropped. Its code is inside the
    commit being installed.
    """
    constraints: list[str] = []
    overrides: list[str] = []
    for line in exported.splitlines():
        line = line.strip()
        requirement = line.split(';', 1)[0]
        if ' @ ' in requirement:
            name, _, rest = line.partition(' @ ')
            wanted = extras.get(name.strip())
            overrides.append(f'{name.strip()}[{",".join(wanted)}] @ {rest}' if wanted else line)
        elif '==' in requirement:
            constraints.append(line)
    return Pins(tuple(constraints), tuple(overrides))


def run_install(
    requirement: str,
    *,
    quiet: bool = True,
    pins: Pins | None = None,
    with_requirements: Sequence[str] = (),
    source_flags: Sequence[str] = (),
) -> None:
    """Install a requirement over any existing tool of that name, held to `pins` when given.

    `with_requirements` are requirements-file lines installed beside the tool,
    the way `--with` and `--with-editable` install them. `--force` is what
    allows an entry point that already exists to be replaced; without it uv
    refuses rather than overwriting.

    The install runs unbounded. Killed part way, it leaves the tool's
    environment half rewritten, which is worse than a slow install.
    """
    executable = shutil.which('uv')
    if not executable:
        raise InstallFailedError(
            'uv is not on PATH, so the tool cannot reinstall itself. '
            'Add the directory holding uv to PATH, or install uv from https://docs.astral.sh/uv/, then run the update again'
        )

    with tempfile.TemporaryDirectory(prefix='pyselfupdate-pins-', ignore_cleanup_errors=True) as scratch:
        held = pins.arguments(Path(scratch)) if pins else []
        beside: list[str] = []
        if with_requirements:
            listed = Path(scratch) / 'with.txt'
            listed.write_text(''.join(f'{line}\n' for line in with_requirements), encoding='utf-8')
            beside = ['--with-requirements', str(listed)]
        command = [executable, 'tool', 'install', '--force', *source_flags, *held, *beside, requirement]
        if quiet:
            command.insert(1, '--quiet')
        completed = run_command(command)

    if completed.returncode != 0:
        raise InstallFailedError(f'uv tool install failed: {_reason(completed)}')


def run_command(
    command: list[str], *, cwd: Path | None = None, timeout: float | None = None, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a program to completion and capture what it printed.

    Every process this package starts goes through here. `timeout` bounds the call in seconds. Past it the program is killed and
    `subprocess.TimeoutExpired` is raised, which each caller turns into its own
    failure. None runs unbounded. `stdin` is written to the program's standard input.
    """
    return subprocess.run(command, cwd=cwd, input=stdin, capture_output=True, text=True, check=False, timeout=timeout)


def _reason(completed: subprocess.CompletedProcess[str]) -> str:
    """Everything a failing command printed.

    No single line carries the reason: uv puts a lock's schema error on its
    first line, git puts a missing ref on its last, and uv wraps a resolution
    failure so its last line is half a sentence.
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

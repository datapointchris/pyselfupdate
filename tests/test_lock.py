"""Holding an update to the release's uv.lock, against real git and real uv.

The stubs in test_updater.py prove which pins reach the install. Only uv can
prove what those pins do, so these tests build repositories and a wheel
directory under tmp and run uv offline against them. Nothing reaches an index,
and nothing is written outside tmp.
"""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest
from conftest import StubSource

from pyselfupdate import Config
from pyselfupdate import update
from pyselfupdate.errors import LockUnreadableError
from pyselfupdate.install import Pins
from pyselfupdate.install import read_lock


def git(repo: Path, *args: str) -> None:
    identity = {'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 'T', 'GIT_COMMITTER_EMAIL': 't@t'}
    subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, env={**os.environ, **identity})


def wheel(index: Path, name: str, version: str) -> None:
    """A pure-Python wheel written by hand, so the directory needs no build backend."""
    info = f'{name}-{version}.dist-info'
    files = {
        f'{name}/__init__.py': '',
        f'{info}/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n',
        f'{info}/WHEEL': 'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    record = [f'{path},sha256={_digest(body)},{len(body.encode())}' for path, body in files.items()]
    files[f'{info}/RECORD'] = '\n'.join([*record, f'{info}/RECORD,,', ''])
    with zipfile.ZipFile(index / f'{name}-{version}-py3-none-any.whl', 'w') as packed:
        for path, body in files.items():
            packed.writestr(path, body)


def _digest(body: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(body.encode()).digest()).rstrip(b'=').decode()


def project(repo: Path, name: str, version: str, dependencies: tuple[str, ...] = ()) -> None:
    """A uv_build project in its own git repository, which uv builds without fetching a backend."""
    (repo / 'src' / name).mkdir(parents=True, exist_ok=True)
    (repo / 'src' / name / '__init__.py').write_text('def main():\n    pass\n')
    (repo / 'pyproject.toml').write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\nrequires-python = ">=3.11"\n'
        f'dependencies = {list(dependencies)!r}\n\n'
        f'[project.scripts]\n{name} = "{name}:main"\n\n'
        '[build-system]\nrequires = ["uv_build"]\nbuild-backend = "uv_build"\n'
    )
    if not (repo / '.git').exists():
        git(repo, 'init', '--quiet', '--initial-branch=main')
    git(repo, 'add', '-A')
    git(repo, 'commit', '--quiet', '-m', version)


def release(repo: Path, name: str, version: str, dependencies: tuple[str, ...], *, locked: bool = True) -> None:
    project(repo, name, version, dependencies)
    if locked:
        subprocess.run(['uv', 'lock', '--quiet'], cwd=repo, check=True, capture_output=True)
        git(repo, 'add', 'uv.lock')
        git(repo, 'commit', '--quiet', '-m', f'lock {version}')
    git(repo, 'tag', f'v{version}')


def installed(tools: Path, tool: str, package: str) -> str:
    python = tools / tool / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    probe = 'import importlib.metadata, sys; print(importlib.metadata.version(sys.argv[1]))'
    return subprocess.run([str(python), '-c', probe, package], check=True, capture_output=True, text=True).stdout.strip()


def receipt(tools: Path, tool: str) -> dict:
    return tomllib.loads((tools / tool / 'uv-receipt.toml').read_text(encoding='utf-8'))['tool']


@pytest.fixture
def offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """uv pointed at a directory of wheels and nothing else, writing only under tmp."""
    index = tmp_path / 'index'
    index.mkdir()
    for name, value in {
        'UV_TOOL_DIR': tmp_path / 'tools',
        'UV_TOOL_BIN_DIR': tmp_path / 'bin',
        'UV_CACHE_DIR': tmp_path / 'cache',
        'UV_FIND_LINKS': index,
        'UV_NO_INDEX': '1',
        'UV_OFFLINE': '1',
        'UV_PYTHON_DOWNLOADS': 'never',
    }.items():
        monkeypatch.setenv(name, str(value))
    return index


@pytest.fixture
def gitdep(tmp_path: Path) -> Path:
    """A dependency installed from git rather than an index, at 1.0.0."""
    repo = tmp_path / 'gitdep'
    project(repo, 'gitdep', '1.0.0')
    return repo


def test_an_update_installs_the_locks_versions_not_the_newest(tmp_path: Path, offline: Path, gitdep: Path) -> None:
    """v2.0.0 locks 1.0.0 of a registry package and of a git one, and 2.0.0 of
    each exists by the time the update runs.

    Both kinds, because they reach uv differently: the registry pin as a
    constraint, the git pin as an override. uv refuses a constraint whose URL
    differs from the one the tool declares.

    The last install is the control. The same tag handed to uv with no lock
    takes 2.0.0 of both, so 1.0.0 is the lock's doing rather than the only
    version on offer. It also drops both lists from the receipt, which is the
    gap `update` closes.
    """
    wheel(offline, 'pindemo', '1.0.0')
    tool = tmp_path / 'locktool'
    dependencies = ('pindemo>=1', f'gitdep @ git+{gitdep.as_uri()}')
    release(tool, 'locktool', '1.0.0', dependencies)
    release(tool, 'locktool', '2.0.0', dependencies)
    subprocess.run(['uv', 'tool', 'install', f'locktool @ git+{tool.as_uri()}@v1.0.0'], check=True, capture_output=True)

    wheel(offline, 'pindemo', '2.0.0')
    project(gitdep, 'gitdep', '2.0.0')
    tools = tmp_path / 'tools'

    result = update(Config(tool='locktool', version='1.0.0', source=StubSource(tag='v2.0.0')))

    assert result.applied
    assert not result.lock_missing
    assert installed(tools, 'locktool', 'locktool') == '2.0.0'
    assert (installed(tools, 'locktool', 'pindemo'), installed(tools, 'locktool', 'gitdep')) == ('1.0.0', '1.0.0')
    held = receipt(tools, 'locktool')
    assert [entry['name'] for entry in held['constraints']] == ['pindemo']
    assert [entry['name'] for entry in held['overrides']] == ['gitdep']

    subprocess.run(['uv', 'tool', 'install', '--force', f'locktool @ git+{tool.as_uri()}@v2.0.0'], check=True, capture_output=True)
    assert (installed(tools, 'locktool', 'pindemo'), installed(tools, 'locktool', 'gitdep')) == ('2.0.0', '2.0.0')
    assert 'constraints' not in receipt(tools, 'locktool')


def test_a_tag_with_no_lock_reads_as_none(tmp_path: Path, offline: Path) -> None:
    tool = tmp_path / 'locktool'
    release(tool, 'locktool', '1.0.0', (), locked=False)

    assert read_lock(tool.as_uri(), 'v1.0.0') is None


def test_the_lock_is_read_at_the_ref_asked_for(tmp_path: Path, offline: Path) -> None:
    """The branch head locks a newer version, so reading it instead would show."""
    wheel(offline, 'pindemo', '1.0.0')
    tool = tmp_path / 'locktool'
    release(tool, 'locktool', '1.0.0', ('pindemo>=1',))
    wheel(offline, 'pindemo', '2.0.0')
    project(tool, 'locktool', '1.1.0', ('pindemo>=2',))
    subprocess.run(['uv', 'lock', '--quiet'], cwd=tool, check=True, capture_output=True)
    git(tool, 'commit', '--quiet', '-am', 'lock 1.1.0')

    assert read_lock(tool.as_uri(), 'v1.0.0') == Pins(constraints=('pindemo==1.0.0',))


def test_a_ref_that_will_not_clone_is_refused(tmp_path: Path, offline: Path) -> None:
    tool = tmp_path / 'locktool'
    release(tool, 'locktool', '1.0.0', ())

    with pytest.raises(LockUnreadableError, match='could not clone .* at v9.9.9'):
        read_lock(tool.as_uri(), 'v9.9.9')


def test_a_lock_uv_will_not_export_is_refused_with_uvs_reason(tmp_path: Path, offline: Path) -> None:
    """A lock from a newer uv is the likely case, and its remedy is on uv's first line, not its last."""
    tool = tmp_path / 'locktool'
    project(tool, 'locktool', '1.0.0')
    (tool / 'uv.lock').write_text('version = 999\n')
    git(tool, 'add', 'uv.lock')
    git(tool, 'commit', '--quiet', '-m', 'a lock from the future')
    git(tool, 'tag', 'v1.0.0')

    with pytest.raises(LockUnreadableError, match='uv export would not read the uv.lock at v1.0.0') as raised:
        read_lock(tool.as_uri(), 'v1.0.0')
    assert 'unsupported schema version' in str(raised.value)


def test_an_empty_list_is_not_passed(tmp_path: Path) -> None:
    """uv warns on an empty requirements file, so a list with no lines gets no flag."""
    flags = Pins(constraints=('click==8.1.7',)).arguments(tmp_path)

    assert flags == ['--constraints', str(tmp_path / 'constraints.txt')]
    assert (tmp_path / 'constraints.txt').read_text(encoding='utf-8') == 'click==8.1.7\n'

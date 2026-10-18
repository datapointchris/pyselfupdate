"""Reading uv's receipt.

The four shapes are taken from real receipts on a machine with these tools
installed, not invented: a pinned git install, a branch-tracking git install, an
index install, and a local checkout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pyselfupdate.errors import NotInstalledError
from pyselfupdate.install import Installation
from pyselfupdate.install import InstallKind
from pyselfupdate.install import read_installation
from pyselfupdate.install import requirement_for
from pyselfupdate.install import tool_dir


@pytest.mark.parametrize('variables', [('UV_TOOL_DIR', 'XDG_DATA_HOME'), ('XDG_DATA_HOME',), ()])
def test_tool_dir_is_where_uv_puts_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variables: tuple[str, ...]) -> None:
    """Asked of uv itself, so every platform CI runs on checks its own default."""
    for name in ('UV_TOOL_DIR', 'XDG_DATA_HOME'):
        if name in variables:
            monkeypatch.setenv(name, str(tmp_path / name.lower()))
        else:
            monkeypatch.delenv(name, raising=False)
    printed = subprocess.run(['uv', 'tool', 'dir'], capture_output=True, text=True, check=True).stdout.strip()
    assert tool_dir() == Path(printed)


def test_reads_a_pinned_git_install(make_receipt) -> None:
    make_receipt('mytool', '{ name = "mytool", git = "https://github.com/x/mytool.git?rev=v4.0.0" }')

    installation = read_installation('mytool')
    assert installation == Installation(
        tool='mytool',
        kind=InstallKind.GIT,
        url='https://github.com/x/mytool.git',
        revision='v4.0.0',
    )


def test_reads_a_branch_tracking_git_install(make_receipt) -> None:
    """No `rev=` means the install follows the default branch.

    The notify gate and `update` refuse it: the version such a build reports
    says nothing about how far behind the checkout is.
    """
    make_receipt('mytool', '{ name = "mytool", git = "https://github.com/x/mytool.git" }')

    installation = read_installation('mytool')
    assert installation.kind is InstallKind.GIT
    assert installation.revision == ''


def test_reads_an_index_install_and_the_flags_reaching_its_sources(make_receipt) -> None:
    """The explicit index is one only a config file declares, which the reinstall reads again.
    The repeated index is one a config file declared and an update passed back."""
    home = '{ name = "home", url = "https://pypi.example.com/simple/", explicit = false, default = false, format = "simple" }'
    make_receipt(
        'codespell',
        '{ name = "codespell", specifier = ">=2.4.0" }',
        'python = "3.14"\n[tool.options]\nindex = ['
        '{ url = "https://pypi.org/simple/", explicit = false, default = true, format = "simple" }, '
        f'{home}, {home}, '
        '{ name = "torch", url = "https://torch.example.com/", explicit = true, default = false, format = "simple" }]\n'
        'extra-index-url = ["https://extra.example.com/simple/"]\n'
        'find-links = ["file:///wheels"]\n'
        'index-strategy = "unsafe-best-match"\n'
        'no-index = true\n'
        'link-mode = "copy"\n',
    )

    installation = read_installation('codespell')
    assert installation.kind is InstallKind.INDEX
    assert installation.source_flags == (
        '--python',
        '3.14',
        '--default-index',
        'https://pypi.org/simple/',
        '--index',
        'home=https://pypi.example.com/simple/',
        '--extra-index-url',
        'https://extra.example.com/simple/',
        '--find-links',
        'file:///wheels',
        '--index-strategy',
        'unsafe-best-match',
        '--no-index',
    )


def test_reads_the_tools_extras_and_rebuilds_what_was_installed_beside_it(make_receipt, tmp_path: Path) -> None:
    """Every shape a `--with` or `--with-editable` writes into a receipt, as a line uv reads back."""
    local, editable = tmp_path / 'localwith', tmp_path / 'editwith'
    make_receipt(
        'mytool',
        '{ name = "mytool", extras = ["fast"], git = "https://github.com/x/mytool.git?rev=v4.0.0" }, '
        """{ name = "withdemo", marker = "python_full_version >= '3.11'", specifier = ">=1" }, """
        '{ name = "gitwith", git = "https://github.com/x/gitwith.git?rev=v1.0.0" }, '
        '{ name = "subwith", git = "https://github.com/x/mono.git?subdirectory=pkg&tag=v2" }, '
        '{ name = "urlwith", url = "https://example.invalid/urlwith-1.0.tar.gz" }, '
        f'{{ name = "localwith", directory = "{local.as_posix()}" }}, '
        f'{{ name = "editwith", editable = "{editable.as_posix()}" }}',
    )

    installation = read_installation('mytool')

    assert installation.extras == ('fast',)
    assert installation.with_requirements == (
        "withdemo>=1 ; python_full_version >= '3.11'",
        'gitwith @ git+https://github.com/x/gitwith.git@v1.0.0',
        'subwith @ git+https://github.com/x/mono.git@v2#subdirectory=pkg',
        'urlwith @ https://example.invalid/urlwith-1.0.tar.gz',
        f'localwith @ {local.as_uri()}',
        f'-e {editable.as_uri()}',
    )


def test_a_requirement_no_line_can_reproduce_does_not_fail_the_read(make_receipt) -> None:
    """A raise here would make the notify gate treat the tool as a local install and print no notice."""
    make_receipt(
        'mytool',
        '{ name = "mytool", git = "https://github.com/x/mytool.git?rev=v4.0.0" }, { name = "plugin", virtual = "/src/plugin" }',
    )

    installation = read_installation('mytool')

    assert installation.kind is InstallKind.GIT
    assert installation.with_requirements == ()


def test_requirement_for_carries_the_tools_extras() -> None:
    installation = Installation('mytool', InstallKind.GIT, url='https://github.com/x/mytool.git', revision='v1.0.0', extras=('fast', 'tui'))
    assert requirement_for(installation, 'v2.0.0') == 'mytool[fast,tui] @ git+https://github.com/x/mytool.git@v2.0.0'


@pytest.mark.parametrize('key', ['directory', 'path', 'editable'])
def test_reads_a_local_install_however_uv_spelled_it(make_receipt, key: str) -> None:
    make_receipt('mytool', f'{{ name = "mytool", {key} = "/src/mytool" }}')

    installation = read_installation('mytool')
    assert installation.kind is InstallKind.LOCAL


def test_a_missing_receipt_is_not_installed(uv_tools: Path) -> None:
    with pytest.raises(NotInstalledError):
        read_installation('absent')


def test_a_receipt_for_a_different_package_is_not_installed(make_receipt) -> None:
    make_receipt('mytool', '{ name = "something-else" }')

    with pytest.raises(NotInstalledError):
        read_installation('mytool')


def test_a_corrupt_receipt_is_not_installed(uv_tools: Path) -> None:
    directory = uv_tools / 'mytool'
    directory.mkdir()
    (directory / 'uv-receipt.toml').write_text('this is not toml [[[', encoding='utf-8')

    with pytest.raises(NotInstalledError):
        read_installation('mytool')


def test_requirement_for_a_git_install_pins_the_ref() -> None:
    installation = Installation('mytool', InstallKind.GIT, url='https://github.com/x/mytool.git', revision='v1.0.0')
    assert requirement_for(installation, 'v2.0.0') == 'mytool @ git+https://github.com/x/mytool.git@v2.0.0'


def test_requirement_for_a_git_install_keeps_a_prefixed_tag_intact() -> None:
    """A nested module's tag is `cli/v1.2.0` and git needs it spelled exactly."""
    installation = Installation('democli', InstallKind.GIT, url='https://github.com/x/monorepo.git', revision='cli/v0.3.0')
    assert requirement_for(installation, 'cli/v0.3.3') == 'democli @ git+https://github.com/x/monorepo.git@cli/v0.3.3'

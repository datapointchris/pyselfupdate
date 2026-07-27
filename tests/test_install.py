"""Reading uv's receipt.

The four shapes are taken from real receipts on a machine with these tools
installed, not invented: a pinned git install, a branch-tracking git install, an
index install, and a local checkout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyselfupdate.errors import NotInstalledError
from pyselfupdate.install import Installation
from pyselfupdate.install import InstallKind
from pyselfupdate.install import read_installation
from pyselfupdate.install import requirement_for
from pyselfupdate.install import tool_dir


def test_tool_dir_prefers_the_explicit_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('UV_TOOL_DIR', str(tmp_path / 'explicit'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    assert tool_dir() == tmp_path / 'explicit'


def test_tool_dir_falls_back_to_xdg_then_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv('UV_TOOL_DIR', raising=False)
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    assert tool_dir() == tmp_path / 'data' / 'uv' / 'tools'

    monkeypatch.delenv('XDG_DATA_HOME', raising=False)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    assert tool_dir() == tmp_path / 'home' / '.local' / 'share' / 'uv' / 'tools'


def test_reads_a_pinned_git_install(make_receipt) -> None:
    make_receipt('syncer', '{ name = "syncer", git = "https://github.com/datapointchris/syncer.git?rev=v4.0.0" }')

    installation = read_installation('syncer')
    assert installation == Installation(
        tool='syncer',
        kind=InstallKind.GIT,
        url='https://github.com/datapointchris/syncer.git',
        revision='v4.0.0',
    )
    assert installation.is_updatable()


def test_reads_a_branch_tracking_git_install(make_receipt) -> None:
    """No `rev=` means the install follows the default branch.

    This is what relate and indy look like today, and it is why the notify gate
    has to treat an empty revision as a dev install: the version such a build
    reports says nothing about how far behind the checkout is.
    """
    make_receipt('relate', '{ name = "relate", git = "https://github.com/datapointchris/relate.git" }')

    installation = read_installation('relate')
    assert installation.kind is InstallKind.GIT
    assert installation.revision == ''


def test_reads_an_index_install(make_receipt) -> None:
    make_receipt('codespell', '{ name = "codespell" }')

    installation = read_installation('codespell')
    assert installation.kind is InstallKind.INDEX
    assert installation.is_updatable()


@pytest.mark.parametrize('key', ['directory', 'path', 'editable'])
def test_reads_a_local_install_however_uv_spelled_it(make_receipt, key: str) -> None:
    make_receipt('dectl', f'{{ name = "dectl", {key} = "/Users/chris/tools/dectl" }}')

    installation = read_installation('dectl')
    assert installation.kind is InstallKind.LOCAL
    assert not installation.is_updatable()


def test_a_missing_receipt_is_not_installed(uv_tools: Path) -> None:
    with pytest.raises(NotInstalledError):
        read_installation('absent')


def test_a_receipt_for_a_different_package_is_not_installed(make_receipt) -> None:
    make_receipt('syncer', '{ name = "something-else" }')

    with pytest.raises(NotInstalledError):
        read_installation('syncer')


def test_a_corrupt_receipt_is_not_installed(uv_tools: Path) -> None:
    directory = uv_tools / 'syncer'
    directory.mkdir()
    (directory / 'uv-receipt.toml').write_text('this is not toml [[[', encoding='utf-8')

    with pytest.raises(NotInstalledError):
        read_installation('syncer')


def test_requirement_for_a_git_install_pins_the_ref() -> None:
    installation = Installation('syncer', InstallKind.GIT, url='https://github.com/x/syncer.git', revision='v1.0.0')
    assert requirement_for(installation, 'v2.0.0') == 'syncer @ git+https://github.com/x/syncer.git@v2.0.0'


def test_requirement_for_a_git_install_keeps_a_prefixed_tag_intact() -> None:
    """A nested module's tag is `cli/v1.2.0` and git needs it spelled exactly."""
    installation = Installation('icb', InstallKind.GIT, url='https://github.com/x/ichrisbirch.git', revision='cli/v0.3.0')
    assert requirement_for(installation, 'cli/v0.3.3') == 'icb @ git+https://github.com/x/ichrisbirch.git@cli/v0.3.3'


def test_requirement_for_an_index_install_uses_a_version_specifier() -> None:
    installation = Installation('codespell', InstallKind.INDEX)
    assert requirement_for(installation, 'v2.4.1') == 'codespell==2.4.1'

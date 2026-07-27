"""check() and update().

`run_install` is patched throughout rather than exercised: it shells out to uv,
which would rebuild a real virtual environment. What is asserted instead is the
requirement string handed to it, which is the part that can be wrong.
"""

from __future__ import annotations

import pytest
from conftest import StubSource

from pyselfupdate import Config
from pyselfupdate import updater as update_module
from pyselfupdate.errors import InstallFailedError
from pyselfupdate.errors import InvalidConfigError
from pyselfupdate.errors import LocalInstallError
from pyselfupdate.errors import NoReleaseError
from pyselfupdate.updater import changelog
from pyselfupdate.updater import check
from pyselfupdate.updater import update


@pytest.fixture
def installs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records the requirements that would have been installed."""
    recorded: list[str] = []
    monkeypatch.setattr(update_module, 'run_install', lambda requirement, quiet=True: recorded.append(requirement))
    return recorded


@pytest.fixture
def pinned(make_receipt):
    make_receipt('demo', '{ name = "demo", git = "https://github.com/x/demo.git?rev=v1.0.0" }')


def config(source: StubSource, version: str = '1.0.0') -> Config:
    return Config(tool='demo', owner='x', version=version, source=source)


def test_check_reports_a_newer_release(pinned) -> None:
    result = check(config(StubSource(tag='v2.0.0')))

    assert result.current == 'v1.0.0'
    assert result.latest == 'v2.0.0'
    assert result.update_available
    assert not result.applied


def test_check_reports_no_update_when_current(pinned) -> None:
    result = check(config(StubSource(tag='v1.0.0')))

    assert not result.update_available
    assert result.latest == 'v1.0.0'


def test_check_treats_an_older_release_as_no_update(pinned) -> None:
    """Running ahead of the published release is not a downgrade prompt."""
    result = check(config(StubSource(tag='v0.9.0'), version='2.0.0'))

    assert not result.update_available


def test_check_never_touches_the_filesystem(pinned, uv_tools) -> None:
    """It is the cheap half of the API, and notify calls it on a hot path."""
    result = check(config(StubSource(tag='v2.0.0')))
    assert result.update_available


def test_a_non_semver_tag_is_rejected(pinned) -> None:
    with pytest.raises(NoReleaseError, match='not a semantic version'):
        check(config(StubSource(tag='release-2024')))


def test_a_version_that_is_not_a_release_is_rejected(pinned) -> None:
    with pytest.raises(LocalInstallError, match='not a release version'):
        check(config(StubSource(tag='v2.0.0'), version='unknown'))


def test_config_requires_a_tool() -> None:
    with pytest.raises(InvalidConfigError, match='tool is required'):
        Config(tool='').resolved()


def test_config_requires_an_owner_without_a_custom_source() -> None:
    with pytest.raises(InvalidConfigError, match='owner is required'):
        Config(tool='demo').resolved()


def test_update_installs_the_pinned_tag(pinned, installs: list[str]) -> None:
    result = update(config(StubSource(tag='v2.0.0')))

    assert result.applied
    assert installs == ['demo @ git+https://github.com/x/demo.git@v2.0.0']


def test_update_preserves_a_prefixed_tag(make_receipt, installs: list[str]) -> None:
    make_receipt('icb', '{ name = "icb", git = "https://github.com/x/ichrisbirch.git?rev=cli/v0.3.0" }')
    source = StubSource(tag='v0.3.3', ref='cli/v0.3.3')

    update(Config(tool='icb', owner='x', version='0.3.0', source=source))

    assert installs == ['icb @ git+https://github.com/x/ichrisbirch.git@cli/v0.3.3']


def test_update_is_a_no_op_when_current(pinned, installs: list[str]) -> None:
    result = update(config(StubSource(tag='v1.0.0')))

    assert not result.applied
    assert not installs


def test_update_refuses_a_local_install(make_receipt, installs: list[str]) -> None:
    make_receipt('demo', '{ name = "demo", editable = "/Users/chris/tools/demo" }')

    with pytest.raises(LocalInstallError, match='update the checkout'):
        update(config(StubSource(tag='v2.0.0')))
    assert not installs


def test_update_refuses_a_branch_tracking_install(make_receipt, installs: list[str]) -> None:
    """relate and indy look like this today, which is why the message says so."""
    make_receipt('demo', '{ name = "demo", git = "https://github.com/x/demo.git" }')

    with pytest.raises(LocalInstallError, match='installed from a branch'):
        update(config(StubSource(tag='v2.0.0')))
    assert not installs


def test_update_refuses_before_checking_the_source(make_receipt) -> None:
    """The receipt is read first, so a refusal costs no network request."""
    make_receipt('demo', '{ name = "demo", editable = "/tmp/demo" }')
    source = StubSource(tag='v2.0.0')

    with pytest.raises(LocalInstallError):
        update(config(source))
    assert source.calls == 0


def test_an_install_failure_propagates(pinned, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(requirement: str, quiet: bool = True) -> None:
        raise InstallFailedError('uv tool install failed: no such ref')

    monkeypatch.setattr(update_module, 'run_install', explode)

    with pytest.raises(InstallFailedError, match='no such ref'):
        update(config(StubSource(tag='v2.0.0')))


def test_changelog_passes_through_to_the_source(pinned) -> None:
    source = StubSource(tag='v2.0.0', subjects=['feat: one', 'fix: two'])

    assert changelog(config(source), 'v1.0.0', 'v2.0.0') == ['feat: one', 'fix: two']


def test_changelog_is_empty_for_a_source_that_cannot_produce_one(pinned) -> None:
    class Bare:
        def latest_release(self):
            raise AssertionError('should not be called')

    assert changelog(Config(tool='demo', owner='x', version='1.0.0', source=Bare()), 'v1.0.0', 'v2.0.0') == []

"""Self-update and update notification for tools installed with `uv tool`.

Two layers, used independently:

    from pyselfupdate import Config, notify, update

    config = Config(tool='syncer', owner='datapointchris')

    notify(config)   # once a day, print one line if behind. Never raises.
    update(config)   # install the latest release. Raises on failure.

`notify` belongs in a CLI's root callback and its result should be ignored.
`update` belongs behind an explicit `<tool> update` command, which is the only
place update failures are ever reported.

The package has no third-party dependencies. The optional typer integration
lives in `pyselfupdate.typercmd` and is installed with the `typer` extra.
"""

from pyselfupdate.config import Config
from pyselfupdate.errors import InstallFailedError
from pyselfupdate.errors import InvalidConfigError
from pyselfupdate.errors import LocalInstallError
from pyselfupdate.errors import NoReleaseError
from pyselfupdate.errors import NotInstalledError
from pyselfupdate.errors import SelfUpdateError
from pyselfupdate.errors import SourceError
from pyselfupdate.github import GitHubSource
from pyselfupdate.install import Installation
from pyselfupdate.install import InstallKind
from pyselfupdate.install import read_installation
from pyselfupdate.notifier import Outcome
from pyselfupdate.notifier import Skip
from pyselfupdate.notifier import enabled
from pyselfupdate.notifier import notify
from pyselfupdate.source import Release
from pyselfupdate.source import Source
from pyselfupdate.state import State
from pyselfupdate.state import read as read_state
from pyselfupdate.updater import Result
from pyselfupdate.updater import changelog
from pyselfupdate.updater import check
from pyselfupdate.updater import update
from pyselfupdate.updater import update_and_reexec

__all__ = [
    'Config',
    'GitHubSource',
    'InstallFailedError',
    'InstallKind',
    'Installation',
    'InvalidConfigError',
    'LocalInstallError',
    'NoReleaseError',
    'NotInstalledError',
    'Outcome',
    'Release',
    'Result',
    'SelfUpdateError',
    'Skip',
    'Source',
    'SourceError',
    'State',
    'changelog',
    'check',
    'enabled',
    'notify',
    'read_installation',
    'read_state',
    'update',
    'update_and_reexec',
]

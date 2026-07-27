"""A ready-made `update` command for CLIs built with typer.

A separate module, installed with the `typer` extra, so that importing
pyselfupdate does not impose a CLI framework on a project using argparse,
click or anything else:

    uv add "pyselfupdate[typer]"

    from pyselfupdate import Config
    from pyselfupdate.typercmd import add_update_command

    config = Config(tool='syncer', owner='datapointchris')
    add_update_command(app, config)

Go solves the same problem with module graph pruning, which lets goselfupdate
keep cobra in a subpackage that a core-only consumer never downloads. Python
has no equivalent, so the dependency is an extra and this module imports typer
at the top -- importing it without the extra fails immediately and clearly
rather than at the first call.
"""

from __future__ import annotations

import sys

import typer

from pyselfupdate.config import Config
from pyselfupdate.errors import SelfUpdateError
from pyselfupdate.updater import changelog
from pyselfupdate.updater import check
from pyselfupdate.updater import update


def add_update_command(app: typer.Typer, config: Config, *, name: str = 'update') -> None:
    """Register `<tool> update` on an existing typer app."""

    @app.command(name=name, help=f'Update {config.tool} to the latest release')
    def update_command(
        check_only: bool = typer.Option(False, '--check', help='Report whether an update is available without installing it'),
        skip_changelog: bool = typer.Option(False, '--no-changelog', help='Do not list the commits between versions'),
    ) -> None:
        run_update(config, check_only=check_only, skip_changelog=skip_changelog)


def run_update(config: Config, *, check_only: bool = False, skip_changelog: bool = False) -> None:
    """Run an update and report it, exiting non-zero on failure.

    Split out from the command so a CLI that wants its own flags, or that nests
    the command under a group, does not have to reimplement the reporting. The
    output format is identical to goselfupdate's, because a fleet that reports
    the same thing three different ways is a fleet you have to read carefully.
    """
    tool = config.tool
    try:
        result = check(config) if check_only else update(config)
    except SelfUpdateError as error:
        typer.echo(f'✗ {tool} upgrade failed: {error}', err=True)
        raise typer.Exit(1) from error

    if not result.update_available:
        typer.echo(f'✓ {tool} already at latest: {result.latest}')
        return

    if result.applied:
        typer.echo(f'✓ {tool} upgraded: {result.current} → {result.latest}')
    else:
        typer.echo(f'✓ {tool} update available: {result.current} → {result.latest}')

    if skip_changelog:
        return

    subjects = changelog(config, result.current, result.latest)
    if not subjects:
        return

    typer.echo('')
    typer.echo('Changes:')
    for subject in subjects:
        typer.echo(f'  • {subject}')

    # The environment this interpreter is running in has just been replaced.
    # Nothing more may be imported, so the process ends here rather than
    # returning into a CLI that might touch a lazily-loaded module.
    if result.applied:
        sys.stdout.flush()

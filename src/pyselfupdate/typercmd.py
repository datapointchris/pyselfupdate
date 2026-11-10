"""A ready-made `update` command for CLIs built with typer.

A separate module, installed with the `typer` extra, so that importing
pyselfupdate does not impose a CLI framework on a project using argparse,
click or anything else:

    uv add "pyselfupdate[typer]"

    from pyselfupdate import Config
    from pyselfupdate.typercmd import add_update_command

    config = Config(tool='mytool', owner='you')
    add_update_command(app, config)

Go solves the same problem with module graph pruning, which lets goselfupdate
keep cobra in a subpackage that a core-only consumer never downloads. Python
has no equivalent, so the dependency is an extra and this module imports typer
at the top -- importing it without the extra fails immediately and clearly
rather than at the first call.
"""

from __future__ import annotations

import typer

from pyselfupdate.config import Config
from pyselfupdate.errors import SelfUpdateError
from pyselfupdate.install import exit_now
from pyselfupdate.updater import changelog
from pyselfupdate.updater import check
from pyselfupdate.updater import install_release
from pyselfupdate.updater import require_updatable


def update_help(tool: str, name: str, markup_mode: str | None) -> str:
    """The update command's help: one line on what it does, then annotated examples.

    Each example keeps a line of its own under each of typer's markup modes,
    which lay out a help's later paragraphs differently. Markdown joins a
    paragraph's lines, so the examples sit in a fenced block there. Rich markup
    keeps the lines as written and would print a fence's backticks. Without
    rich, click rewraps every paragraph that does not open with its `\\b`
    marker.
    """
    command = f'{tool} {name}'
    width = len(command) + len(' --no-changelog') + 2
    examples = '\n'.join(
        f'{call:<{width}}{purpose}'
        for call, purpose in (
            (command, 'install the newest and list its commits'),
            (f'{command} --check', 'only report whether a newer one exists'),
            (f'{command} --no-changelog', 'install it without listing its commits'),
        )
    )
    if markup_mode == 'markdown':
        block = f'```text\n{examples}\n```'
    elif markup_mode == 'rich':
        block = examples
    else:
        block = f'\b\n{examples}'
    return f'Update {tool} to the latest release\n\nExamples:\n\n{block}'


def add_update_command(app: typer.Typer, config: Config, *, name: str = 'update') -> None:
    """Register `<tool> update` on an existing typer app."""

    @app.command(name=name, help=update_help(config.tool, name, app.rich_markup_mode))
    def update_command(
        check_only: bool = typer.Option(False, '--check', help='Report whether an update is available without installing it'),
        skip_changelog: bool = typer.Option(False, '--no-changelog', help='Do not list the commits between versions'),
    ) -> None:
        run_update(config, check_only=check_only, skip_changelog=skip_changelog)


def run_update(config: Config, *, check_only: bool = False, skip_changelog: bool = False) -> None:
    """Run an update and report it, exiting non-zero on failure.

    Split out from the command so a CLI that wants its own flags, or that nests
    the command under a group, does not have to reimplement the reporting. The
    output format is identical to goselfupdate's, so tools built on either
    library report an update in the same words.

    `check_only` reads the receipt first, as an update does, and refuses the
    same installs. `updater.installed_from_release` says why a version alone
    cannot decide.

    Everything that reaches the network or imports a module happens before the
    install; after it, this process only prints what it already holds and exits
    without unwinding. An import after the install can name a module the new
    release no longer ships, which the install has already deleted. An HTTP
    client's lazy import of its transport is one, and it raises ImportError
    after the update has succeeded.
    """
    tool = config.tool
    try:
        installation = require_updatable(config)
        result = check(config)
    except SelfUpdateError as error:
        typer.echo(f'✗ {tool} update failed: {error}', err=True)
        raise typer.Exit(1) from error

    if not result.update_available:
        typer.echo(f'✓ {tool} already at latest: {result.latest}')
        _echo_warnings(tool, result.index_warning)
        return

    subjects = [] if skip_changelog else changelog(config, result.current, result.latest)

    if check_only:
        typer.echo(f'✓ {tool} update available: {result.current} → {result.latest}')
        _echo_warnings(tool, result.index_warning)
        _echo_changes(subjects)
        return

    try:
        installed = install_release(config, result, installation)
    except SelfUpdateError as error:
        typer.echo(f'✗ {tool} update failed: {error}', err=True)
        raise typer.Exit(1) from error

    typer.echo(f'✓ {tool} updated: {result.current} → {result.latest}')
    _echo_warnings(tool, installed.lock_warning, installed.index_warning)
    _echo_changes(subjects)
    exit_now()


def _echo_warnings(tool: str, *warnings: str) -> None:
    for warning in warnings:
        if warning:
            typer.echo(f'! {tool}: {warning}', err=True)


def _echo_changes(subjects: list[str]) -> None:
    if not subjects:
        return
    typer.echo('')
    typer.echo('Changes:')
    for subject in subjects:
        typer.echo(f'  • {subject}')

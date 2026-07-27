# Changelog

All notable changes to this project are documented here. Maintained by
python-semantic-release from conventional commits; edit the unreleased section
by hand only.

## Unreleased

### Added

- `notify` — a once-a-day update check that prints one line and nothing else.
  Gated on an opt-out environment variable, CI detection, terminal detection,
  the install kind, and a 24 hour interval.
- `check` / `update` / `update_and_reexec` / `changelog` — the explicit update
  path, for a `<tool> update` command.
- `GitHubSource` over `urllib`, with support for prefixed tag streams
  (`cli/v1.2.3`), prereleases, and token authentication.
- Install-kind detection from uv's own `uv-receipt.toml`, refusing local,
  editable and branch-tracking installs.
- A shared `autoupdate.json` state schema, identical across goselfupdate,
  pyselfupdate and bashselfupdate.
- `pyselfupdate.typercmd`, behind the `typer` extra.

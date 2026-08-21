# pyselfupdate

Self-update and update notification for Python CLIs installed with `uv tool`.

Two things, used independently: tell the user once a day that a newer release
exists, and install it when they ask. No runtime dependencies.

```python
from pyselfupdate import Config, notify, update

config = Config(tool='mytool', owner='you')

notify(config)  # once a day, one line if behind. Never raises.
update(config)  # install the latest release. Raises on failure.
```

## Install

```bash
uv add pyselfupdate

# with the ready-made typer command
uv add "pyselfupdate[typer]"
```

Requires Python 3.11+.

## Why

A CLI distributed with `uv tool install` has no way to tell its user a newer
version exists, so it silently drifts. The usual fix drags an HTTP client, a
TOML parser and a version library into a tool that had none of them.

This package has zero runtime dependencies — `urllib` for the network,
`tomllib` for uv's receipt, and its own semver implementation — and CI enforces
that by importing every module into a virtual environment containing nothing
else.

## notify

Put it in your CLI's root callback and ignore the result:

```python
import typer
from pyselfupdate import Config, notify

app = typer.Typer()
CONFIG = Config(tool='mytool', owner='you')


@app.callback()
def main() -> None:
    notify(CONFIG)
```

Once per 24 hours, if a newer release exists, one line goes to stderr **after**
your command's own output:

```text
mytool v1.4.0 available (running v1.3.2) — run `mytool update`
```

It never raises, never installs anything, and never prints an error. A failed
check is recorded in the state file and swallowed, because an update notice
must not be able to break the command the user actually typed.

Nothing is printed when any of these hold:

| Condition | Why |
| --- | --- |
| `NO_AUTO_UPDATE` or `MYTOOL_NO_AUTO_UPDATE` is set | Opted out |
| `CI`, `BUILD_NUMBER`, `RUN_ID`, `GITHUB_ACTIONS`, `CODESPACES` | Not a human |
| stdout or stderr is not a terminal | `mytool list > out 2>&1` must stay clean |
| Installed from a local path, an editable checkout, or a branch | Nothing to compare against |
| Checked within the interval | One request per day, not per invocation |

Presence-only, any value: `NO_AUTO_UPDATE=0` disables it, the same way
[`NO_COLOR`](https://no-color.org) works. Set the interval separately with
`AUTO_UPDATE_INTERVAL=6h` or `MYTOOL_AUTO_UPDATE_INTERVAL=30m`.

## update

```python
from pyselfupdate import Config, check, update

result = check(config)  # no filesystem, no install
if result.update_available:
    print(result.current, '->', result.latest)

result = update(config)  # installs, raises on failure
```

Or take the whole command:

```python
from pyselfupdate.typercmd import add_update_command

add_update_command(app, CONFIG)  # gives you `mytool update [--check]`
```

`update` runs `uv tool install --force`, which **rebuilds the virtual
environment the running interpreter lives in**. Unlike replacing a Unix binary
— where the process holds an inode and is untouched — this pulls modules out
from under a live process, so anything imported afterwards may fail in ways
that are hard to read. Make it the last thing your process does, then call
`exit_now` — or use `update_and_reexec` to replace the process with the new
version immediately.

That cuts both ways: anything you want to *print* after the install has to be
fetched before it. `run_update` resolves its changelog first for exactly this
reason, and a caller that needs its own steps in between composes the three
pieces `update` is made of rather than working around it:

```python
installation = require_updatable(config)  # refuses a checkout, costs nothing
result = check(config)  # network, environment still intact
notes = changelog(config, result.current, result.latest)
install_release(config, result, installation)
print(notes)
exit_now()
```

## What will not be updated

Read from uv's own receipt, written at install time, rather than guessed at
runtime:

| Receipt | Result |
| --- | --- |
| `git = "...git?rev=v1.2.3"` | Updatable |
| `name = "mytool"` (from an index) | Updatable |
| `git = "...git"` with no `rev` | Refused — tracks a branch, so its version says nothing about how far behind it is |
| `directory` / `path` / `editable` | Refused — reinstalling would discard a working copy |

A tool that cannot be identified at all is treated as local and left alone.

## Configuration

```python
Config(
    tool='mytool',  # required: uv tool name, state dir, env prefix
    owner='you',  # GitHub owner
    repo='mytool',  # defaults to tool
    package='mytool',  # distribution name, defaults to tool
    version='1.2.3',  # defaults to the installed distribution's metadata
    token='',  # see Authentication below; you almost certainly want the default
    token_func=None,  # a source of your own, tried before $GITHUB_TOKEN_COMMAND
    tag_prefix='',  # e.g. 'cli/' for tags like cli/v1.2.3
    allow_prerelease=False,
    source=None,  # a custom Source; anything with latest_release()
)
```

## Authentication

**Authenticated by default. Configure nothing.** `GitHubSource` runs `gh auth
token` when a request is about to be made, and sends what it prints.

The alternative is not "no credential". It is 60 requests an hour, charged **per
IP address** and shared with every other anonymous caller behind the same
egress. A default that has to be opted into is a default nobody sets.

Four sources, first non-empty wins:

| Source | Set by | Default |
| --- | --- | --- |
| `Config.token` | you, in code | unset |
| `$GITHUB_TOKEN`, then `$GH_TOKEN` | whoever runs your CLI | unset |
| `token_func()` | you, in code | unset |
| `$GITHUB_TOKEN_COMMAND` | whoever runs your CLI | `gh auth token` |

`$GITHUB_TOKEN_COMMAND` both redirects and disables, which is what a switch has
to do to be worth having:

```bash
GITHUB_TOKEN_COMMAND='pass show github/token'   # use this instead
GITHUB_TOKEN_COMMAND='op read op://vault/gh/token'
GITHUB_TOKEN_COMMAND=''                         # run nothing, stay anonymous
```

It never raises. A command that is not installed, exits non-zero, or takes
longer than ten seconds degrades to an unauthenticated request, which still
works against a public repository.

`token_func` is now only for a credential neither the environment nor a command
can produce. It is called lazily, for the same reason the command is: the notify
gate resolves a `Config` on every invocation and declines most of them without
reaching the network, and a subprocess in front of that gate is the entire cost
worth avoiding.

**This lives on `GitHubSource`, not on `Config`.** A credential is the host's
business — a `Source` for another forge brings its own variable and its own
command, and nothing above the `Source` protocol learns either name.

## State

`${XDG_STATE_HOME:-~/.local/state}/<tool>/autoupdate.json`, written atomically:

```json
{
  "schema": 1,
  "tool": "mytool",
  "checked_at": "2026-07-26T15:07:15Z",
  "checked_at_epoch": 1785078435,
  "current_version": "v1.3.2",
  "latest_version": "v1.4.0",
  "last_error": ""
}
```

State, not config and not cache: it persists across runs, it is not authored by
the user, and deleting it changes behaviour rather than merely costing a
recompute. That is `XDG_STATE_HOME` by the Base Directory specification, and it
is where `gh` puts the same thing.

The timestamp is written **before** the network call. `gh` stamps only on
success, so a rate-limited or offline user re-hits the API on every invocation
until the window resets; an interval exists to bound the request rate, and only
this ordering actually does that.

## Siblings

The same two-layer design in other languages. **`update` exists in all three;
the `notify` layer, the shared `autoupdate.json` schema and the
`NO_AUTO_UPDATE` contract are implemented in this library and in its bash
sibling, and are still to be added to goselfupdate** — until then goselfupdate
provides the update half only.

- [goselfupdate](https://github.com/datapointchris/goselfupdate) — replaces a Go binary
- [bashselfupdate](https://github.com/datapointchris/bashselfupdate) — moves a git checkout to its newest tag

Version precedence is deliberately identical across all three, so a tool and
its siblings never disagree about which release is newer.

## Licence

MIT

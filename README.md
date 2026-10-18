# pyselfupdate

Self-update and update notification for Python CLIs installed with `uv tool`.

A CLI installed with `uv tool install` has no way to tell its user that a newer
release exists, so it silently drifts. This package adds two layers, used
independently. `notify` prints one line, at most once a day, when a newer
release exists. `update` installs that release when the user asks.

`uv tool upgrade` re-resolves the requirement a tool was installed with, so a
tool installed at a release tag stays at that tag. `update` asks the release
source which tag is newest and reinstalls at that tag. A git install is held to
that tag's `uv.lock`.

The package has no runtime dependencies. It uses `urllib` for the network,
`tomllib` for uv's receipt and lock, and its own semver implementation. CI
installs it alone into an empty virtual environment and imports every module
except the typer integration.

## Install

```bash
uv add pyselfupdate            # the library alone, with no dependencies
uv add 'pyselfupdate[typer]'   # adds typer and the ready-made `update` command
```

Python 3.11 is the floor. It is the first release carrying `tomllib`, which
reads uv's receipt and lock without a TOML dependency.

## Starting from scratch

Wire both layers into a typer CLI. `tool` is the name `uv tool install` gives
the tool, and `owner` is the GitHub account its releases are published under.

```python
import typer

from pyselfupdate import Config, notify
from pyselfupdate.typercmd import add_update_command

app = typer.Typer()
CONFIG = Config(tool='mytool', owner='you')  # releases at github.com/you/mytool


@app.callback()
def main() -> None:
    notify(CONFIG)  # never raises; ignore what it returns


add_update_command(app, CONFIG)  # registers `mytool update`
```

Then publish a release, install the tool at its tag, and ask it:

```bash
gh release create v1.0.0 --generate-notes  # a GitHub release, not a bare tag
uv tool install 'mytool @ git+https://github.com/you/mytool@v1.0.0'  # at a tag, never a branch
mytool update --check                      # ✓ mytool already at latest: v1.0.0
```

The running version is read from the installed distribution's metadata, so a
release's tag has to match the version its package declares. A tool versioned
from its git tags gets that for free.

The install names a tag because an install from a branch, a commit or a local
checkout is never updated. Its version says nothing about how far behind it
is. [What may be updated](#what-may-be-updated-is-read-from-uvs-receipt-not-guessed-from-the-version)
lists every case.

A private repository needs a credential. `gh auth login` is enough, because
the check runs `gh auth token` by default.

**When the notice never appears, run `mytool update --check` first.** It runs
the two steps that can fail, reading uv's receipt and asking the release
source, and prints what stopped it. The notice stays silent on both. Every
other reason it stays silent is in the table under `notify`.

## `notify` — one line after the command's output, never an error

Once per 24 hours, when a newer release exists, one line goes to stderr:

```text
mytool v1.4.0 available (running v1.3.2) — run `mytool update`
```

By default the line is printed at interpreter exit, so it lands after the
command's own output rather than inside it.

`notify` never raises, never installs anything, and never prints an error. A
check that fails is recorded in the state file and swallowed, because an update
notice must not be able to break the command the user typed.

Nothing is printed when any of these hold:

| Condition | Why |
| --- | --- |
| `NO_AUTO_UPDATE` or `MYTOOL_NO_AUTO_UPDATE` is set | Opted out |
| `--help` or `-h` comes before any `--` on the command line | typer runs the callback even for a subcommand's help screen, which is not work |
| stdout or stderr is not a terminal | `mytool list > out 2>&1` must stay clean |
| `CI`, `BUILD_NUMBER`, `RUN_ID`, `GITHUB_ACTIONS` or `CODESPACES` is set | Not a human |
| Installed from a local path, an editable checkout, a branch or a commit | Nothing to compare against |
| Checked within the interval | One request per interval, not per invocation |

The opt-out is presence-only, so `NO_AUTO_UPDATE=0` disables the notice, the
same way [`NO_COLOR`](https://no-color.org) works. The interval has its own
pair, `MYTOOL_AUTO_UPDATE_INTERVAL` falling back to `AUTO_UPDATE_INTERVAL`.
Each takes `30m`, `6h`, `7d` or a bare number of seconds. The tool's form of
each variable is its name uppercased, with `-` written as `_`.

A command writing into a pager, a log or a structured-output mode calls
`notify(config, interactive=False)`. Nothing about the streams reveals that.

## `update` — rebuilds the environment it runs in, so it runs last

`update` runs `uv tool install --force`, which rebuilds the virtual environment
the running interpreter lives in. Replacing a Unix binary leaves a running
process untouched, because the process holds an inode. Rebuilding a venv pulls
modules out from under a live interpreter, so anything imported afterwards can
fail in ways that are hard to read.

So make `update` the last thing the process does, then call `exit_now`. Or call
`update_and_reexec`, which replaces the process with the release it installed,
running the same command line. Its caller never sees the `Result`, so it writes
`lock_warning` to stderr itself before the re-exec.

`check` reports what `update` would install, and installs nothing:

```python
from pyselfupdate import Config, check, require_updatable

config = Config(tool='mytool', owner='you')

require_updatable(config)  # reads uv's receipt; raises on an install update refuses
result = check(config)  # one request to the release source
if result.update_available:
    print(result.current, '->', result.latest)
```

`check` compares the running version with the newest release, and reads
nothing else. A branch build's version can rank below the release it is ahead
of. Three commits past `v1.2.3`, a tool versioned from git tags builds as
`1.2.3-post.3+<commit>`, which semver ranks below `v1.2.3`. So call
`require_updatable` before reporting what `check` found. It refuses every
install `update` refuses.

The typer extra carries the whole command. `add_update_command(app, config)`
registers `mytool update`. `run_update(config, check_only=...)` is the same
reporting, for a CLI that declares the command itself. Both read the receipt
before the network, so `mytool update --check` refuses the same installs
`update` does, printing the refusal and exiting 1. Both fetch the changelog
before the install, because nothing can be imported after it.

A caller that needs its own steps between the check and the install composes
the three pieces `update` is made of:

```python
import sys

from pyselfupdate import Config, changelog, check, exit_now, install_release, require_updatable

config = Config(tool='mytool', owner='you')

installation = require_updatable(config)  # refuses before any request
result = check(config)  # the network, while the environment is intact
notes = changelog(config, result.current, result.latest)  # fetched before the install, printed after
installed = install_release(config, result, installation)  # a no-op when nothing is newer
print('\n'.join(notes))
if installed.lock_warning:
    print(installed.lock_warning, file=sys.stderr)
exit_now()  # flushes, then exits without unwinding through code that may import
```

Every error is a `SelfUpdateError`, and each cause has its own subclass, so a
caller matches a type rather than message text:

| Error | Raised when |
| --- | --- |
| `InvalidConfigError` | `tool` is empty, or `owner` is empty without a custom `source` |
| `LocalInstallError` | The install is a local checkout, a branch or a commit, or the running version is not a semantic version |
| `NotInstalledError` | uv's receipt is missing or unreadable, or holds a `--with` requirement an update would drop |
| `NoReleaseError` | The source publishes no release, or its newest tag is not a semantic version |
| `SourceError` | The source could not be reached, or answered with something unusable |
| `LockUnreadableError` | The release's `uv.lock` could not be read, so nothing was installed |
| `InstallFailedError` | `uv tool install` ran and failed, or uv is not on `PATH` |

### A git install is held to the release's `uv.lock`

`uv tool install` never reads a lock. Handed a git requirement, it resolves
every dependency to its newest version, so a tool would run on a set its CI
never tested. `update` therefore clones the release's tag and exports its
`uv.lock`. It passes registry pins as `--constraints`, and URL pins, such as a
git dependency, as `--overrides`. uv records both in the tool's receipt, so a
later `uv tool upgrade` stays held to them.

| The tag | What `update` does |
| --- | --- |
| Carries a `uv.lock` | Installs the locked versions |
| Carries none | Installs unlocked and sets `lock_missing`. `lock_warning` names the tag, and `run_update` and `update_and_reexec` print it |
| Will not clone, uv will not export its lock, or git or uv is not on `PATH` | Raises `LockUnreadableError` and installs nothing |

An install from an index reads no lock, because a wheel carries none. It
takes the release as a floor, `mytool>=2.0.0`, rather than a pin. uv records
the specifier in the receipt, and `uv tool upgrade` refuses to move an exact
pin.

An update reinstalls the tool's own extras, as in `mytool[fast]`, with their
dependencies held to the lock. Every `--with` and `--with-editable`
requirement is passed back to uv as well.

`uv tool install --force` reads nothing back from the receipt it replaces. So
an update passes back the receipt's `--python` and the flags naming where its
packages come from: `--index`, `--default-index`, `--index-url`,
`--extra-index-url`, `--find-links`, `--no-index`, `--index-strategy` and
`--keyring-provider`. Without them, a tool installed with
`--index https://pypi.example.com/simple/` resolves its update against PyPI
alone and is not found. An `explicit` index is left out, because only a config
file can declare one and the reinstall reads that file again. Dropping any
other recorded option changes how the tool resolves or builds, never whether it
is found.

## What may be updated is read from uv's receipt, not guessed from the version

uv writes `uv-receipt.toml` into the tool's directory at install time. It
records where the tool came from, which the running program cannot know:

| Receipt | Result |
| --- | --- |
| `git = "...git?rev=v1.2.3"` | Updatable |
| `name = "mytool"` (from an index) | Updatable |
| `git = "...git"` with no `rev` | Refused: it tracks the default branch, so its version says nothing about how far behind it is |
| `git = "...git?rev=main"`, or a `rev` naming a commit | Refused: uv writes `rev=` for whatever followed the `@`, so a `rev` counts as a release only when it is `tag_prefix` followed by a semantic version |
| `directory`, `path` or `editable` | Refused: reinstalling would discard a working copy |
| A `--with` requirement no requirements-file line reproduces, such as a relative path | Refused with `NotInstalledError`: the update would drop it |

`update` and `mytool update --check` refuse every row marked Refused. The
notice stays silent on each of them except the `--with` row, because a newer
release is still worth knowing about there.

A tool whose receipt is missing or unreadable fails closed. The notice treats
it as local, and `update` raises `NotInstalledError`.

A caller with a custom `source` whose refs carry a prefix sets `tag_prefix` to
match. Without it, a tag install from that source is refused as a branch
install.

## `Config` — the tool's name, and every default derived from it

```python
from pyselfupdate import Config

config = Config(
    tool='mytool',  # required: the uv tool name, the state directory, the variable prefix
    owner='you',  # GitHub owner; required unless source is given
    repo='mytool',  # defaults to tool
    package='mytool',  # the distribution the version is read from; defaults to tool
    version='1.2.3',  # defaults to that distribution's installed metadata
    token='',  # see Authentication below; you almost certainly want the default
    token_func=None,  # a source of your own, tried before $GITHUB_TOKEN_COMMAND
    timeout=10.0,  # seconds, per request
    tag_prefix='',  # 'cli/' for tags like cli/v1.2.3
    allow_prerelease=False,
    source=None,  # a custom Source; defaults to a GitHubSource built from the fields above
)
```

`source` is the seam for releases published somewhere other than GitHub. A
`Source` is anything with `latest_release()` returning a `Release`. A source
that also has `changelog(from_ref, to_ref)`, returning commit subjects, gets a
changelog in `mytool update`. One without it gets none, and that is not an
error.

## Authentication — on by default, through `gh auth token`

**Configure nothing.** `GitHubSource` runs `gh auth token` when a request is
about to be made, and sends what it prints.

The alternative is not "no credential". It is 60 requests an hour, charged
**per IP address** and shared with every other anonymous caller behind the same
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

`token_func` is for a credential neither the environment nor a command can
produce. It is called lazily, for the same reason the command is. The notify
gate resolves a `Config` on every invocation and declines most of them without
reaching the network, and a subprocess in front of that gate is the entire
cost worth avoiding.

**The variable names and the default command belong to `GitHubSource`, not to
`Config`.** A credential is the host's business. A `Source` for another code
host brings its own variable and its own command, and nothing above the
`Source` protocol learns either name.

## State — one file per tool per machine, stamped before the network call

`${XDG_STATE_HOME:-~/.local/state}/<tool>/autoupdate-<machine>.json`, written
atomically:

```json
{
  "tool": "mytool",
  "schema": 1,
  "checked_at": "2026-07-26T15:07:15Z",
  "checked_at_epoch": 1785078435,
  "current_version": "v1.3.2",
  "latest_version": "v1.4.0",
  "last_error": ""
}
```

State, not config and not cache: it persists across runs, it is not authored by
the user, and deleting it changes behavior rather than merely costing a
recompute. That is `XDG_STATE_HOME` by the Base Directory specification, and it
is where `gh` puts the same thing.

`<machine>` is the bare hostname, domain dropped and lowercased, from
`pyselfupdate.state.machine()`. It is `unknown` when the hostname cannot be
read. It is in the name because both fields that vary describe one machine:
the version installed here, and the instant this box last checked. A state
directory shared between machines, by a file syncer or a network home
directory, otherwise has two writers on one path. It reports whichever wrote
last as the state of all of them.

The timestamp is written **before** the network call. `gh` stamps only on
success, so a rate-limited or offline user re-hits the API on every invocation
until the window resets. An interval exists to bound the request rate, and only
this ordering does that.

A gate that declines to check writes nothing, so a missing file means the
network was never reached. A check that failed leaves its error in
`last_error`. `read_state('mytool')` reads the file back.

## Siblings — the same two layers for a Go binary and a git checkout

All three carry both layers. They share the `autoupdate-<machine>.json` schema,
the machine derivation that names it, and the `NO_AUTO_UPDATE` and
`AUTO_UPDATE_INTERVAL` variables. They do not share an API, because "update"
means a different operation in each.

- [goselfupdate](https://github.com/datapointchris/goselfupdate) — replaces a Go binary
- [bashselfupdate](https://github.com/datapointchris/bashselfupdate) — moves a git checkout to its newest tag

Version precedence is deliberately identical across all three, so a tool and
its siblings never disagree about which release is newer.

## License

MIT

# pyselfupdate — Claude Code instructions

A public Python library, not a CLI. There is no entry point and nothing to
install as a tool; it ships to PyPI and is consumed by Python CLIs installed
with `uv tool`.

Unlike most repos here, this one is written to be used by strangers. Treat the
public API, the README and the docstrings as the product — a change that is
merely convenient for the internal consumers is not automatically right.

It is the Python sibling of goselfupdate and bashselfupdate, which the README
links. The three deliberately share conventions and version precedence. All
three have a notify layer — goselfupdate's is `autoupdate/autoupdate.go`. The
state-file schema (`autoupdate-<machine>.json`) and the environment variables
(`NO_AUTO_UPDATE` and `AUTO_UPDATE_INTERVAL`, each with a `<TOOL>_` form) are
shared across all three, so a rename in one breaks the other two. They do
**not** share an API, because "update" means three different operations — see
"Why the API differs from goselfupdate".

## Layout

| Path | Holds |
| --- | --- |
| `version.py` | Semantic version comparison, replacing `packaging` |
| `source.py` | The `Source` protocol and `Release` |
| `github.py` | `GitHubSource`, over `urllib` |
| `install.py` | Reading uv's receipt, reading a tag's `uv.lock`, running `uv tool install`, re-exec |
| `updater.py` | `check`, `update`, `changelog` |
| `notifier.py` | The gate, the interval, the notice |
| `state.py` | The shared `autoupdate-<machine>.json` schema, and the machine derivation that names it |
| `typercmd.py` | The typer `update` command. The only module importing typer |

`updater.py` and `notifier.py` are named for the module, not the function they
export. `update.py` would be shadowed by `from pyselfupdate.updater import
update` in `__init__.py`, so `import pyselfupdate.update` would silently resolve
to the function and fail on attribute access. Do not rename them back.

## Constraints that must not regress

- **The package has zero runtime dependencies**, and CI enforces it two ways: the
  declared list must be empty, and every core module must import in a venv
  containing nothing else. That second check is this repo's own — adding an update
  notice to a CLI should not drag in an HTTP client, a TOML parser and a version
  library.

  **`httpx2` was weighed against this and declined**, though it is the HTTP
  client a new Python CLI would otherwise get. It arrives with `httpcore2`,
  `anyio`, `idna`, `h11` and `truststore` — six packages, about 57,000 lines,
  agreed by `uv tree` and a metadata walk. A consumer that declares no HTTP
  client of its own would inherit the whole closure for having wanted a
  self-update check.

  What it would buy does not reach here. This package's own code makes one
  HTTP request, to `api.github.com`, for JSON. Every other fetch is a
  subprocess: `install.run_install` runs `uv tool install --force` and
  `install.read_lock` runs `git clone`. So there is no CDN redirect for a token
  to leak into, and nothing needs a client object, connection reuse or
  streaming. Revisit if a second HTTP need appears — at that point the closure
  is buying something.
- **typer stays confined to `typercmd.py`,** installed via the `typer` extra. A
  library keeps its dependencies off its consumers' surface, so a caller using
  argparse or click never installs typer. The extra is what Python needs to get
  what Go gets free from module graph pruning.
- **The floor is Python 3.11 and CI tests against it.** It is deliberately below
  the 3.13 a new project would start on: 3.11 is what `tomllib` requires, which
  is what lets uv's receipt and lock be read without a dependency. Raising it
  excludes callers.
- **Errors are typed.** A new failure mode gets a class in `errors.py`; callers
  must never have to match on message text.
- **The machine in the state filename is derived identically in all three
  siblings** — bare hostname, domain dropped, lowercased, `unknown` when it
  cannot be read. A library deriving it differently puts one box's state under
  two names, and neither box then sees the other's.

The self-update design rules are shared with the two siblings: notify never
raises or prints, the timestamp is stamped before the network call, version
comparison stays byte-compatible with the siblings, and the state schema is
shared across all three. Where they land in this repo:
`test_precedence_follows_the_specification` is the ordering-matrix assertion,
and the notify path's no-raise guarantee is a design rule rather than a
collection of individual try/excepts.

## Why the API differs from goselfupdate

goselfupdate replaces a running binary: it needs `Source`, `Verifier`,
archive extraction and an atomic rename. A uv tool has no binary and no archive
— `uv tool install --force` rebuilds the virtual environment the running
interpreter lives in. So roughly 80% of goselfupdate's surface has no analog
here, and this package has one thing goselfupdate does not need: `reexec`.

That difference is load-bearing, not cosmetic. Replacing a Unix binary is safe
under a running process, which holds an inode rather than a path. Replacing a
venv is not: it pulls modules out from under a live interpreter, so anything
imported afterwards can fail unreadably. `update` must therefore be the last
thing a process does before exiting or re-execing, and there is no background
mode.

## Detecting a build that must not be updated

goselfupdate rejects a dev build by its version string, because Go stamps a
recognizable pseudo-version onto local builds. That signal does not exist here,
and a Go pseudo-version is in any case perfectly valid semver — `version.py`
parses it happily, and `test_a_go_pseudo_version_is_valid_semver` documents why
that is correct.

The Python signal is **uv's own receipt** (`<uv tool dir>/<tool>/uv-receipt.toml`),
written at install time, which says whether the tool came from a tag, a branch
or a local path. That is strictly better than inferring it at runtime: the
installer knows how the tool got there and the running program does not. Two
kinds are refused — `LOCAL` (a path or editable checkout), and `GIT` whose
`rev=` is not `tag_prefix` followed by a semantic version (a branch or a commit,
so its version says nothing about how far behind it is). uv writes `rev=` for
whatever followed the `@`. `@main` lands in the same field as `@v1.2.3`, so
only the value tells them apart. `updater.installed_from_release` is the one
copy of that rule. `update`, `update --check` and the notify gate all reach it.

A version check cannot stand in for the receipt. A branch build versioned from
git tags reports `1.2.3-post.3+<sha>`. That is valid semver, and it ranks below
the `v1.2.3` it is ahead of. Comparing it offers the older release as an update.

Failing closed matters here: a tool that cannot be identified is treated as
local and never nagged.

## Testing

The two-layer shape — `StubSource` in `conftest.py` for the logic, `test_github.py`
against a local `http.server` for the wire — is how a network client is tested:
offline against a stub, plus one test against a local server. Everything runs
offline.

The install has the same two layers. `StubLock` stands in for `read_lock` in the
updater and typercmd tests. `test_lock.py` runs real git and real uv against
repositories and a wheel directory it builds under tmp, with uv offline and
every tool directory redirected there. No other test shows what the pins do to
an install.

**Terminal detection is injected, never faked by reassigning `sys.stdout`.**
pytest's capture reinstalls its own streams between fixture setup and the test
call, so a reassigned `sys.stdout` silently reverts and a gate test passes for
the wrong reason. Injection also lets a caller writing into a pager or a log
say so, which nothing about the streams reveals. The seams here are
`notify(interactive=...)` and `is_interactive(streams)`.

`clean_environment` is autouse and clears every variable the gate reads, so a
developer's own `CI=1` cannot change the result.

## Releasing

Push a conventional commit to main. python-semantic-release decides the version,
tags, and creates the GitHub release; a second job in the same workflow builds
and publishes to PyPI with Trusted Publishing.

The tag is the version. pyproject.toml declares it dynamic, and hatchling reads
it through uv-dynamic-versioning, so the release writes no commit and the tag
sits on the merged commit. The publish job checks out the tag for that reason: a
build anywhere else names the previous release plus a distance.

Both jobs are in one workflow because **a tag pushed with `GITHUB_TOKEN` does
not trigger another workflow run** — a publish keyed on `release: published`
would never fire.

**A PyPI version cannot be replaced, only yanked, and the name is claimed
forever.** Same class of irreversibility as a Go module tag once the proxy has
cached it.

The changelog is the GitHub release body, written by python-semantic-release
from the commit subjects in the release. Nothing commits it back to the repo, so
the commit message is where a change explains itself to a consumer.

After releasing, bump each consumer with `uv add 'pyselfupdate[typer]>=<version>'`,
or without `[typer]` where it declares none. That raises the floor and moves the
lock together. A consumer's locked install runs the version its lock names, so
a lock left behind keeps the old release. During development, point a consumer
at the local checkout with `tool.uv.sources` rather than publishing a version
per change.

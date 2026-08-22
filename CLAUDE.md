# pyselfupdate — Claude Code instructions

A public Python library, not a CLI. There is no entry point and nothing to
install as a tool; it ships to PyPI and is consumed by the Python CLIs in
`~/tools/` and `~/webapps/`.

Unlike most repos here, this one is written to be used by strangers. Treat the
public API, the README and the docstrings as the product — a change that is
merely convenient for the internal consumers is not automatically right.

It is the Python sibling of `~/tools/goselfupdate` and `~/tools/bashselfupdate`.
The three deliberately share conventions and version precedence. All three now
have a notify layer — goselfupdate's is `autoupdate/autoupdate.go`. The
state-file schema and the environment-variable contract are shared across all
three, so a rename in one breaks the other two; see
`standards/release.md` § Self-update. They do **not** share an API,
because "update" means three different operations — see "Why the API differs
from goselfupdate".

## Layout

| Path | Holds |
| --- | --- |
| `version.py` | Semantic version comparison, replacing `packaging` |
| `source.py` | The `Source` protocol and `Release` |
| `github.py` | `GitHubSource`, over `urllib` |
| `install.py` | Reading uv's receipt, running `uv tool install`, re-exec |
| `updater.py` | `check`, `update`, `changelog` |
| `notifier.py` | The gate, the interval, the notice |
| `state.py` | The shared `autoupdate.json` schema |
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
- **typer stays confined to `typercmd.py`,** installed via the `typer` extra.
  Containment is `standards/repo-structure.md` § "A library keeps its dependencies
  off its consumers' surface", which names this repo; the extra is what Python needs
  to get what Go gets free from module graph pruning.
- **The floor is Python 3.11 and CI tests against it.** This is a sanctioned
  exception to the fleet's 3.13 floor, recorded in `standards/python.md`:
  3.11 is what `tomllib` requires, which is what lets uv's receipt be read
  without a dependency. Raising it excludes callers.
- **Errors are typed.** A new failure mode gets a class in `errors.py`; callers
  must never have to match on message text.

The self-update design rules — notify never raises or prints, the timestamp is
stamped before the network call, version comparison stays byte-compatible with
the siblings, and the state schema is shared across all three — are
`standards/release.md` § Self-update, and are not restated here. Where
they land in this repo: `test_precedence_follows_the_specification` is the
ordering-matrix assertion, and the notify path's no-raise guarantee is a design
rule rather than a collection of individual try/excepts.

## Why the API differs from goselfupdate

goselfupdate replaces a running binary: it needs `Source`, `Verifier`,
archive extraction and an atomic rename. A uv tool has no binary and no archive
— `uv tool install --force` rebuilds the virtual environment the running
interpreter lives in. So roughly 80% of goselfupdate's surface has no analogue
here, and this package has one thing goselfupdate does not need: `reexec`.

That difference is load-bearing, not cosmetic. Replacing a Unix binary is safe
under a running process, which holds an inode rather than a path. Replacing a
venv is not: it pulls modules out from under a live interpreter, so anything
imported afterwards can fail unreadably. `update` must therefore be the last
thing a process does before exiting or re-execing, and there is no background
mode.

## Detecting a build that must not be updated

goselfupdate rejects a dev build by its version string, because Go stamps a
recognisable pseudo-version onto local builds. That signal does not exist here,
and a Go pseudo-version is in any case perfectly valid semver — `version.py`
parses it happily, and `test_a_go_pseudo_version_is_valid_semver` documents why
that is correct.

The Python signal is **uv's own receipt** (`<uv tool dir>/<tool>/uv-receipt.toml`),
written at install time, which says whether the tool came from a tag, a branch
or a local path. That is strictly better than inferring it at runtime: the
installer knows how the tool got there and the running program does not. Three
kinds are refused — `LOCAL` (a path or editable checkout), and `GIT` with no
`rev=` (tracking a branch, so its version says nothing about how far behind it
is). The second is what `relate` and `indy` look like today.

Failing closed matters here: a tool that cannot be identified is treated as
local and never nagged.

## Testing

The two-layer shape — `StubSource` in `conftest.py` for the logic, `test_github.py`
against a local `http.server` for the wire — is `standards/testing.md` § "A network
client tests offline against a stub, plus one test against a local server".
Everything runs offline.

**Terminal detection is injected, never faked by reassigning `sys.stdout`** —
`standards/python.md` § "Inject terminal detection; never monkeypatch it" carries
the pytest-capture reason and the pager affordance. The seams here are
`notify(interactive=...)` and `_is_interactive(streams)`.

`clean_environment` is autouse and clears every variable the gate reads, so a
developer's own `CI=1` cannot change the result.

## Releasing

Push a conventional commit to main. python-semantic-release decides the version,
tags, and creates the GitHub release; a second job in the same workflow builds
and publishes to PyPI with Trusted Publishing.

Both jobs are in one workflow because **a tag pushed with `GITHUB_TOKEN` does
not trigger another workflow run** — a publish keyed on `release: published`
would never fire. There is a `publish.yml.disabled` in `logsift` making exactly
that mistake.

**A PyPI version cannot be replaced, only yanked, and the name is claimed
forever.** Same class of irreversibility as a Go module tag once the proxy has
cached it. Update `CHANGELOG.md` in the same commit.

After releasing, bump consumers with `uv add pyselfupdate@latest`. During
development, point a consumer at the local checkout with `tool.uv.sources`
rather than publishing a version per change.

# CHANGELOG


## v0.1.0 (2026-07-27)

### Features

- Initial library
  ([`50dea4c`](https://github.com/datapointchris/pyselfupdate/commit/50dea4c5f8382859c7505f589d8ef87cd957183d))

Update notification and self-update for Python CLIs installed with `uv tool`, and the Python sibling
  of goselfupdate. Two layers used independently: `notify` prints one line a day and never raises,
  `update` installs a release and raises on failure.

Zero runtime dependencies, enforced two ways in CI: the declared list must be empty, and every core
  module must import in a virtual environment containing nothing else. That is why semver comparison
  is implemented here rather than taken from packaging, and why uv's receipt is read with tomllib.
  typer is an extra, confined to typercmd.

The API deliberately diverges from goselfupdate. Replacing a Go binary is safe under a running
  process, which holds an inode rather than a path; `uv tool install --force` rebuilds the venv the
  interpreter is living in, so update must be the last thing a process does and there is no
  background mode. What the two libraries do share is the state file schema, the environment
  variable contract, and version precedence.

Dev installs are detected from uv's own receipt rather than a version string. The installer knows
  how a tool got there and the running program does not, so this refuses local, editable and
  branch-tracking installs by reading what uv recorded at install time.

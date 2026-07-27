# CHANGELOG


## v0.2.0 (2026-07-27)

### Documentation

- State accurately what the siblings share today
  ([`2cc54f5`](https://github.com/datapointchris/pyselfupdate/commit/2cc54f5f53204c95023e2c9a19a7206008fc1715))

Both new libraries claimed all three share the state schema and the NO_AUTO_UPDATE contract.
  goselfupdate has neither: it implements the update half only and has no notify layer, so the claim
  was false for a third of the family it described.

### Features

- Resolve an expensive token lazily with token_func
  ([`4c15a87`](https://github.com/datapointchris/pyselfupdate/commit/4c15a878dd91e106ae7c0a869d1132c06c3172aa))

A private repository needs a real token, and the usual source is the `gh` CLI — a subprocess.
  Assigning that to Config.token means it runs wherever the Config is built, and the notify gate
  resolves one on every invocation to then decline in microseconds. relate is the case that surfaced
  it: a private repo whose CLI would have paid a process spawn on every command.

token_func is consulted where the token is actually used, inside the request, so it runs only when a
  request is made. It sits last in the precedence chain, leaving an explicit token and both
  environment variables unaffected. Mirrors goselfupdate's Config.TokenFunc.

### Refactoring

- Drop the unwritten skip field from the state schema
  ([`d7e432c`](https://github.com/datapointchris/pyselfupdate/commit/d7e432c6aab539ccbc761e9c8284e1217798c6fc))

Nothing ever wrote it: the only assignment set it to the empty string, because a gate that declines
  to check deliberately does not write the file at all. That absence is what makes "no state file"
  observable proof the network was never touched, so a skip-reason field has no writer by design
  rather than by oversight.

Surfaced while writing the bash sibling, where shellcheck flagged the captured-and-unused reason
  that would have populated it. A documented schema field with no writer is worse than no field, and
  this schema is shared across all three libraries.

No behaviour changes, so no release is cut.


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

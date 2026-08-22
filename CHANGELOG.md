# CHANGELOG


## v0.3.2 (2026-08-22)

### Bug Fixes

- Never check for updates while printing a help screen
  ([`f2b4c8b`](https://github.com/datapointchris/pyselfupdate/commit/f2b4c8bfd3f4034229d486b54730de736a5c774e))

Click and Typer run a group's callback before answering a subcommand's --help, so a notice called
  from that callback fires while a help screen is printed. Nothing a notice is for happens there,
  and the check costs a releases-API call and a state write every time.

It lands hardest on a reader that walks a tool's help. Measured 2026-08-22: reading doit's twenty
  commands meant twenty-one invocations, each one reaching this, and eighteen tools on that machine
  write their autoupdate state on plain --help.

The reason is a new Skip.HELP and it is checked in enabled(), so `<tool> update --why` reports it
  like every other reason.

A -- ends the scan, because everything after it is the command's own argument. Skipping when it
  should not have costs a missed notice, which is the cheap direction to be wrong.


## v0.3.1 (2026-08-21)

### Bug Fixes

- **github**: Tell bandit the token-command constants hold no token
  ([`2abb09e`](https://github.com/datapointchris/pyselfupdate/commit/2abb09e6aaad4c926b139a33a2473ee8b71bd3c5))

B105 reads any name carrying "token" as a credential, so TOKEN_COMMAND_ENV and DEFAULT_TOKEN_COMMAND
  were reported as hardcoded passwords. One holds the name of an environment variable and the other
  the text of a command.

bandit runs in a custom block of validate.yml and had no local counterpart, so the finding could
  only surface as a red push. The pre-commit hook runs the same command against the same config,
  which is what keeps the two from disagreeing.


## v0.3.0 (2026-08-21)

### Build System

- **precommit**: Resync to forge toolchain 14
  ([`3b8ecf9`](https://github.com/datapointchris/pyselfupdate/commit/3b8ecf9dc317ce925e69d137737a00e5dfed6619))

### Chores

- **lint**: Disable SC1091/SC1090 from the forge toolchain
  ([`667b4fd`](https://github.com/datapointchris/pyselfupdate/commit/667b4fddd157b9ba24ec1251a1216c33a7882715))

- **pyproject**: Raise assertion verbosity instead of test verbosity
  ([`8254919`](https://github.com/datapointchris/pyselfupdate/commit/82549192e304936735fa8093e0f5919e7ab2b087))

A failing assertion truncated its diff and printed "use -vv to show", so the reader re-ran the whole
  suite to see it. addopts = "-vv" answered that by raising test-list verbosity as well, which is a
  different question: a green run printed a line per test and said nothing. verbosity_assertions
  raises only the half that was wanted.

Written by the forge pyproject die.

### Continuous Integration

- Drop the duplicated lint job, keep bandit
  ([`4be5cab`](https://github.com/datapointchris/pyselfupdate/commit/4be5caba092b8de77c7e603a03799e92d57aaf49))

The bespoke lint job ran ruff, ruff format and mypy, all three of which the generated workflow
  already runs. It resolved ruff through `uv run` from the dev group's `ruff>=0.7.0` floor, which
  the lock puts at 0.16.0, while validate.yml and the pre-commit hook both pin 0.12.5 — two linters
  four minor versions apart on one tree, free to disagree.

bandit was the only step the baseline did not cover, so it moves into a custom:after:python block
  rather than being lost. mypy coverage widens in the move: the baseline checks `.` where this
  checked `src`.

Same removal bashselfupdate had for shellcheck and shfmt, and the same place logsift's bandit went.

- Regenerate validate.yml at toolchain 16
  ([`23dbbb8`](https://github.com/datapointchris/pyselfupdate/commit/23dbbb8b2897637df9807dcba9b86c617ece95bc))

Catches this repo up with the version manifest: StyLua pinned to a release rather than latest, a
  reworded bats discovery note, and double quotes in the node block. Only the blocks this repo
  declares are affected.

Triggers and job structure are unchanged.

- Rename bespoke workflow to Bespoke CI
  ([`9664c4d`](https://github.com/datapointchris/pyselfupdate/commit/9664c4d0521199771db1062a2045934d3a13ce84))

The generated validate.yml also declares `name: CI`, so one commit produced two indistinguishable CI
  rows carrying the same createdAt to the second. Ordering that pair by timestamp is undefined, so a
  failing bespoke run read as green whenever the baseline passed. This repo is where that was
  measured: no-dependencies failed while the generated job passed on the same sha, and a fleet sweep
  reported the repo green.

Display name only. Required status checks match job names, and a release gate names the workflow
  file rather than its name.

### Documentation

- Cite the standards without a machine path
  ([`a7453e8`](https://github.com/datapointchris/pyselfupdate/commit/a7453e8902416b085a2fdfcc17e0d82d69032bdd))

The citation carried an absolute path from one machine's layout. What a reader needs is the file and
  the section, and those do not move.

- Cross-reference release.md instead of restating nine of its rules
  ([`2ffada4`](https://github.com/datapointchris/pyselfupdate/commit/2ffada453c495a81064bdd9556a9bcc7055c0aaa))

The self-update design rules were reproduced here with the same worked examples release.md already
  carries. Keep only what is specific to this repo — the 3.11 floor as a sanctioned exception, typed
  errors, and where the ordering matrix is asserted — and point at the standard for the rest.

Also corrects the claim that goselfupdate has no notify layer; it does, at autoupdate/autoupdate.go.

### Features

- **github**: Authenticate by default, resolved inside the source
  ([`0aff222`](https://github.com/datapointchris/pyselfupdate/commit/0aff22265d2c6c5f4c4f8ffe3765487359a1354d))

The library refused to shell out to gh, on the principle that it should not spawn a subprocess a
  caller did not ask for. The consequence was that all seventeen consumers had to paste the same
  five-line helper, and eleven never did — so eleven tools were asking GitHub anonymously.

Anonymous is not no credential. It is 60 requests an hour charged per IP address, shared by every
  host behind one egress. Measured 2026-08-21 across one household: two machines checking on a timer
  held that pool at zero for whole hours and every tool that asked anonymously was refused,
  including on a laptop that had run nothing.

GitHubSource now resolves GITHUB_TOKEN_COMMAND, defaulting to gh auth token. One lever that both
  redirects and disables: set it to another command to use that, set it to empty to stay anonymous.
  Named for what it produces rather than for turning something off, because a NO_GH_TOKEN cannot say
  use this other source and reads as a claim about whether one exists rather than an instruction
  about whether to use one.

It lives on GitHubSource rather than Config because the credential is the host's business. A Source
  for another forge brings its own variable and its own command, and nothing above the Source
  protocol learns either.

The rate-limit message now names which ceiling was hit. One sentence for both told whoever hit the
  authenticated limit to supply a token the request already carried.

Tests default the command to empty, so the suite no longer passes or fails on whether the developer
  happens to be logged in to gh.


## v0.2.2 (2026-08-04)

### Bug Fixes

- Report the update in the verb that ran it
  ([`d534189`](https://github.com/datapointchris/pyselfupdate/commit/d534189df5f7eb1782b85bf261b0c2f88ebdc137))

The command is `update`, and --check and the daily notice both say "update", but the success and
  failure lines said "upgraded" and "upgrade failed". One command, one vocabulary.

### Chores

- Add .planning to gitignore
  ([`d10f941`](https://github.com/datapointchris/pyselfupdate/commit/d10f941e32765b686cfab7755d0dee0c4908bc01))

- **config**: Adopt the standard pyright section
  ([`2bc0aa8`](https://github.com/datapointchris/pyselfupdate/commit/2bc0aa828770f634a7d2dc951e072673dd0e15a0))

Synced from forge pyproject template. With no [tool.pyright] section the editor LSP settings
  applied, and their ignore = ["*"] suppressed every diagnostic. A config file takes precedence over
  those settings, so basedpyright now reports against the same "standard" mode as the rest of the
  portfolio instead of reporting nothing.

- **config**: Record the keys the pyproject sync owns
  ([`1f3e5a9`](https://github.com/datapointchris/pyselfupdate/commit/1f3e5a974e059370794662fbbcca5c6b62927333))

forge now writes [tool.forge] managed, listing the exact keys the standard sets. Deletion on a later
  sync is scoped to that record, so dropping a key from the template retracts it here without having
  to guess which settings belong to this project.

Purely additive: nothing else in this file changed.

- **toolchain**: Adopt the generated configs and CI
  ([`d0be7cf`](https://github.com/datapointchris/pyselfupdate/commit/d0be7cf5a1709f28841b021bfce85e67e3f597ab))

Brings the repo onto forge toolchain manifest 11.

bandit, refurb and pyupgrade drop out: pyupgrade is ruff's UP rules, already selected, and the other
  two are the manifest's deliberate narrowing to the rule set every repo actually runs.

### Documentation

- Flush dormant markdownlint violations
  ([`faa11a2`](https://github.com/datapointchris/pyselfupdate/commit/faa11a219377708e9774cab521fed3d15fcf511e))

markdownlint only runs on the files a commit touches, so unmodified docs accumulate violations
  invisibly. The toolchain sync bumps markdownlint to v0.47, which added MD060, and runs --all-files
  — surfacing every one of them at once, in the middle of an unrelated change.

Table separators are normalized to the compact `| --- |` style MD060 expects, which --fix cannot
  repair; everything else is markdownlint --fix.

- Format the composed-update example as ruff wants it
  ([`9842930`](https://github.com/datapointchris/pyselfupdate/commit/98429308a00bebaa94abdc5ee263fd126e63a003))

CI runs `ruff format --check .`, which formats python blocks inside markdown; the pre-commit hook
  only sees .py files, so aligned comments in a README example pass locally and fail there.

- Stop normalizing the generated CHANGELOG
  ([`109706e`](https://github.com/datapointchris/pyselfupdate/commit/109706e4cc28cc08facc4bd6e31f2f0a47e98d57))

semantic-release regenerates CHANGELOG.md on every release, so a markdownlint fix there is undone on
  the next one and comes back as a conflict when a local commit rebases onto the release.


## v0.2.1 (2026-07-27)

### Bug Fixes

- Do every fallible step before the install, then exit
  ([`14c6c69`](https://github.com/datapointchris/pyselfupdate/commit/14c6c6967dd6960841726d3d2b3fa64828f3b81f))

syncer 4.0.0's own update command crashed immediately after a successful upgrade: it fetched its
  changelog with httpx once uv had already rewritten the virtual environment underneath it, and
  httpx's lazy import of httpcore resolved against a directory where 4.3.0 had just dropped that
  dependency. The upgrade was fine; the process reporting on it was not.

run_update had the same shape. It fetched the changelog after installing, and the comment claiming
  the process ends there only flushed stdout before returning into typer. github.py uses urllib, so
  the fetch survives today, but a custom Source is public API and nothing stops one importing httpx.

Everything that touches the network or imports now happens before the install, and exit_now ends the
  process without unwinding through click's error handling or interpreter shutdown, both of which
  may import.

update() is unchanged for callers; it is now the composition of require_updatable, check and
  install_release, which is what lets run_update slot the changelog fetch between the second and
  third.

### Chores

- Keep markdownlint off the generated changelog
  ([`dc47b15`](https://github.com/datapointchris/pyselfupdate/commit/dc47b1592713238aaebff5d90a09fb58971a45f8))

semantic-release writes CHANGELOG.md from its own template, blank lines and all, so --fix rewrites
  it on every --all-files run and the change comes straight back at the next release.


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

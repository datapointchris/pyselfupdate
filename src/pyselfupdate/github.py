"""GitHub as a release source.

Uses `urllib.request` rather than httpx or requests, because the whole point of
this package is that adding it to a project adds nothing else.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime

from pyselfupdate.errors import NoReleaseError
from pyselfupdate.errors import SelfUpdateError
from pyselfupdate.errors import SourceError
from pyselfupdate.source import Release

API = 'https://api.github.com'
DEFAULT_TIMEOUT = 10.0


# B105 reads any name carrying "token" as a credential. Both hold the name of a
# variable and the text of a command; neither ever holds a secret.
TOKEN_COMMAND_ENV = 'GITHUB_TOKEN_COMMAND'  # nosec B105

DEFAULT_TOKEN_COMMAND = 'gh auth token'  # nosec B105
"""What runs when nothing overrides it.

Authenticating is the default because the alternative is not "no credential" but
"sixty requests an hour, charged per IP address and shared by every host behind
one egress". Measured 2026-08-21 across one household: two machines checking on a
timer held that pool at zero for whole hours, and every tool on the network that
asked anonymously was refused. A default that has to be opted into is a default
nobody sets, and eleven of fourteen tools here had not.
"""


def token_from_env() -> str:
    """A token from the environment, or an empty string."""
    return os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN') or ''


def token_from_command() -> str:
    """A token from `$GITHUB_TOKEN_COMMAND`, or from `gh auth token`.

    One lever that both redirects and disables, which is what a switch has to do
    to be worth having. Unset runs the default; set to a command runs that one;
    set to empty runs nothing and the request goes out unauthenticated.

        GITHUB_TOKEN_COMMAND='pass show github/token'
        GITHUB_TOKEN_COMMAND=''

    Named for the thing it produces rather than for turning something off. A
    `NO_GH_TOKEN` cannot say "use this other source" and reads as a claim about
    whether one exists rather than an instruction about whether to use one.

    This lives on `GitHubSource` rather than on `Config`, because the credential
    is the host's business. A source for another forge brings its own variable
    and its own command, and nothing above `Source` learns either name.

    Never raises. Every failure -- no such command, a non-zero exit, a binary
    that is not installed -- degrades to an unauthenticated request, which still
    works against a public repository.
    """
    command = os.environ.get(TOKEN_COMMAND_ENV, DEFAULT_TOKEN_COMMAND)
    argv = shlex.split(command)
    if not argv:
        return ''

    # Resolved to a full path so the call is not a partial-path lookup, which is
    # what bandit's B607 is about and what makes a PATH entry able to answer.
    binary = shutil.which(argv[0])
    if not binary:
        return ''
    try:
        result = subprocess.run([binary, *argv[1:]], capture_output=True, text=True, check=False, timeout=COMMAND_TIMEOUT_SECONDS)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout.strip() if result.returncode == 0 else ''


COMMAND_TIMEOUT_SECONDS = 10.0
"""A credential helper that hangs must not hang the command someone typed.

`gh` is a local read, but the variable takes an arbitrary command and a password
manager can block on a locked vault or a touch prompt that nobody is there to
answer -- and the update check runs unattended on a timer.
"""


@dataclass
class GitHubSource:
    """Releases published on GitHub.

    Authenticates by default. Without a credential GitHub allows 60 API requests
    an hour *per IP address* -- shared with every other anonymous caller behind
    the same egress -- and rejects private repositories outright.

    Four sources, first non-empty wins: `token`, `$GITHUB_TOKEN`/`$GH_TOKEN`,
    `token_func`, then `$GITHUB_TOKEN_COMMAND` defaulting to `gh auth token`.
    """

    owner: str
    repo: str
    token: str = ''

    # A source of a caller's own, tried before the command. Called only when a
    # request is actually about to be made.
    #
    # It exists because a credential can be expensive to obtain -- a keychain
    # prompt, a subprocess -- and a caller that resolves such a token eagerly
    # into `token` pays for it on every invocation, including the ones where the
    # notify gate declines to check at all. That gate is otherwise free, and a
    # spawn in front of it is the entire cost.
    #
    # Reaching for `gh` no longer needs one: that is the default below. This is
    # for a credential neither the environment nor a command can produce.
    token_func: Callable[[], str] | None = None

    timeout: float = DEFAULT_TIMEOUT
    allow_prerelease: bool = False

    # Selects one release stream in a repository publishing several, as in
    # "cli/" for tags of the form cli/v1.2.3. GitHub's /releases/latest cannot
    # express this -- it returns whichever release is newest overall -- so a
    # prefix switches to listing and filtering.
    tag_prefix: str = ''

    headers: dict[str, str] = field(default_factory=dict)

    # Resolved once per source, because a check that also fetches a changelog
    # makes several requests and the command behind this can be a vault unlock.
    # None means "not yet asked", which an empty string cannot say.
    _resolved_token: str | None = field(default=None, init=False, repr=False, compare=False)

    def _credential(self) -> str:
        if self._resolved_token is None:
            self._resolved_token = self.token or token_from_env() or (self.token_func() if self.token_func else '') or token_from_command()
        return self._resolved_token

    def latest_release(self) -> Release:
        if self.tag_prefix or self.allow_prerelease:
            return self._latest_from_list()
        return self._latest_from_endpoint()

    def changelog(self, from_ref: str, to_ref: str) -> list[str]:
        """Commit subjects between two tags, newest last.

        Returns an empty list rather than raising: a missing changelog is not a
        reason to fail an update that already succeeded.
        """
        if not from_ref or not to_ref or from_ref == to_ref:
            return []
        path = f'/repos/{self.owner}/{self.repo}/compare/{_quote(from_ref)}...{_quote(to_ref)}'
        try:
            payload = self._get(path)
        except SelfUpdateError:
            # Every failure, not just transport ones. A tag that GitHub cannot
            # compare -- because the older one was deleted, or the release was
            # cut from a different branch -- returns 404, and reporting that as
            # a failed update after the install already succeeded would be a lie.
            return []
        subjects = []
        for commit in payload.get('commits') or []:
            message = (commit.get('commit') or {}).get('message') or ''
            subject = message.splitlines()[0].strip() if message else ''
            if subject:
                subjects.append(subject)
        return subjects

    def _latest_from_endpoint(self) -> Release:
        payload = self._get(f'/repos/{self.owner}/{self.repo}/releases/latest')
        tag = payload.get('tag_name') or ''
        if not tag:
            raise NoReleaseError(f'{self.owner}/{self.repo} publishes no release')
        return self._release(payload, tag)

    def _latest_from_list(self) -> Release:
        # Releases come back newest-first, so the first match is the latest.
        payload = self._get(f'/repos/{self.owner}/{self.repo}/releases?per_page=100')
        if not isinstance(payload, list):
            raise SourceError(f'unexpected response listing releases for {self.owner}/{self.repo}')

        for entry in payload:
            if entry.get('draft'):
                continue
            if entry.get('prerelease') and not self.allow_prerelease:
                continue
            tag = entry.get('tag_name') or ''
            if not tag or not tag.startswith(self.tag_prefix):
                continue
            return self._release(entry, tag)

        wanted = f' with prefix {self.tag_prefix!r}' if self.tag_prefix else ''
        raise NoReleaseError(f'{self.owner}/{self.repo} publishes no release{wanted}')

    def _release(self, payload: dict, tag: str) -> Release:
        return Release(
            tag=tag.removeprefix(self.tag_prefix),
            ref=tag,
            url=payload.get('html_url') or '',
            notes=payload.get('body') or '',
        )

    def _get(self, path: str):
        url = f'{API}{path}'

        # urlopen honours file:, ftp: and data: as readily as http:. API is a
        # constant here, but it is a module attribute a caller can reassign --
        # tests do exactly that -- so the scheme is checked rather than assumed.
        # Without this, setting it to a file: URL turns a release check into an
        # arbitrary file read.
        scheme = urllib.parse.urlparse(url).scheme
        if scheme not in ('https', 'http'):
            raise SourceError(f'refusing to fetch {scheme or "a schemeless URL"}: only http and https are allowed')

        request = urllib.request.Request(url)
        request.add_header('Accept', 'application/vnd.github+json')
        request.add_header('X-GitHub-Api-Version', '2022-11-28')
        request.add_header('User-Agent', 'pyselfupdate')
        token = self._credential()
        if token:
            request.add_header('Authorization', f'Bearer {token}')
        for name, value in self.headers.items():
            request.add_header(name, value)

        try:
            # B310 is a call blacklist rather than a dataflow check, so it fires
            # on urlopen regardless of the scheme guard above. That guard, and
            # test_a_non_http_scheme_is_refused, are the actual defence.
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310  # nosec B310
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise _http_error(self.owner, self.repo, error, authenticated=bool(token)) from error
        except urllib.error.URLError as error:
            raise SelfUpdateSourceFailure(f'cannot reach {API}: {error.reason}') from error
        except json.JSONDecodeError as error:
            raise SelfUpdateSourceFailure(f'{API}{path} returned invalid JSON') from error


class SelfUpdateSourceFailure(SourceError):
    """A transport or protocol failure, as opposed to a missing release.

    Distinct from NoReleaseError so a caller can tell "GitHub is unreachable"
    apart from "this repository has published nothing", which are the same HTTP
    status for a private repository and want different messages.
    """


def _http_error(owner: str, repo: str, error: urllib.error.HTTPError, *, authenticated: bool) -> SelfUpdateError:
    if error.code == 404:
        # A private repository reached without a token is indistinguishable
        # from one that does not exist, and saying so is more useful than
        # reporting a bare 404.
        return NoReleaseError(f'{owner}/{repo} has no releases, or is private and no token was supplied')
    if error.code in (401, 403):
        remaining = error.headers.get('x-ratelimit-remaining') if error.headers else None
        if remaining == '0':
            return SelfUpdateSourceFailure(_rate_limit_message(error, authenticated=authenticated))
        return SelfUpdateSourceFailure(f'GitHub refused the request for {owner}/{repo} ({error.code})')
    return SelfUpdateSourceFailure(f'GitHub returned {error.code} for {owner}/{repo}')


def _rate_limit_message(error: urllib.error.HTTPError, *, authenticated: bool) -> str:
    """Which ceiling was hit, and what to do about that one.

    The two are different problems with different fixes, and one sentence for
    both sent the wrong instruction to whichever case it was not written for.
    Telling someone to supply a token when the request already carried one is
    the worse half: it reads as advice to go and configure something that is
    already configured.

    The authenticated case should be rare, so it says when the ceiling lifts
    rather than what to change -- there is nothing to change.
    """
    resets = _reset_clock(error)
    if authenticated:
        return f"GitHub's authenticated rate limit (5,000/hour) is exhausted{resets}"
    return (
        "GitHub's anonymous rate limit (60/hour, shared by every host on this IP) is exhausted"
        f'{resets}. Run `gh auth login`, or set GITHUB_TOKEN, for the 5,000/hour authenticated limit'
    )


def _reset_clock(error: urllib.error.HTTPError) -> str:
    """`; resets at 14:22 UTC`, or nothing when the header is absent or unusable.

    A wall-clock time rather than "in 43 minutes", because the message is read
    out of a state file long after the request that produced it and a duration
    would be counted from the wrong instant.
    """
    header = error.headers.get('x-ratelimit-reset') if error.headers else None
    try:
        moment = datetime.fromtimestamp(int(header or ''), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return ''
    return f'; resets at {moment:%H:%M} UTC'


def _quote(ref: str) -> str:
    return urllib.parse.quote(ref, safe='')

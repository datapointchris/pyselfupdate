"""GitHub as a release source.

Uses `urllib.request` rather than httpx or requests, because the whole point of
this package is that adding it to a project adds nothing else.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field

from pyselfupdate.errors import NoReleaseError
from pyselfupdate.errors import SelfUpdateError
from pyselfupdate.errors import SourceError
from pyselfupdate.source import Release

API = 'https://api.github.com'
DEFAULT_TIMEOUT = 10.0


def token_from_env() -> str:
    """A token from the environment, or an empty string.

    Deliberately does not shell out to `gh auth token`. A library should not
    spawn a subprocess a caller did not ask for, and a caller who wants that
    behaviour can pass the token in. This matches goselfupdate.
    """
    return os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN') or ''


@dataclass
class GitHubSource:
    """Releases published on GitHub.

    Without a token GitHub allows 60 API requests per hour per IP address and
    rejects private repositories outright.
    """

    owner: str
    repo: str
    token: str = ''

    # Resolves a token when `token` is empty and neither environment variable is
    # set. Called only when a request is actually about to be made.
    #
    # It exists because a credential can be expensive to obtain -- a keychain
    # prompt, a `gh auth token` subprocess -- and a caller that resolves such a
    # token eagerly into `token` pays for it on every invocation, including the
    # ones where the notify gate declines to check at all. That gate is
    # otherwise free, and a subprocess spawn in front of it is the entire cost.
    token_func: Callable[[], str] | None = None

    timeout: float = DEFAULT_TIMEOUT
    allow_prerelease: bool = False

    # Selects one release stream in a repository publishing several, as in
    # "cli/" for tags of the form cli/v1.2.3. GitHub's /releases/latest cannot
    # express this -- it returns whichever release is newest overall -- so a
    # prefix switches to listing and filtering.
    tag_prefix: str = ''

    headers: dict[str, str] = field(default_factory=dict)

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
        token = self.token or token_from_env() or (self.token_func() if self.token_func else '')
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
            raise _http_error(self.owner, self.repo, error) from error
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


def _http_error(owner: str, repo: str, error: urllib.error.HTTPError) -> SelfUpdateError:
    if error.code == 404:
        # A private repository reached without a token is indistinguishable
        # from one that does not exist, and saying so is more useful than
        # reporting a bare 404.
        return NoReleaseError(f'{owner}/{repo} has no releases, or is private and no token was supplied')
    if error.code in (401, 403):
        remaining = error.headers.get('x-ratelimit-remaining') if error.headers else None
        if remaining == '0':
            return SelfUpdateSourceFailure('GitHub API rate limit exceeded; set GITHUB_TOKEN to raise it')
        return SelfUpdateSourceFailure(f'GitHub refused the request for {owner}/{repo} ({error.code})')
    return SelfUpdateSourceFailure(f'GitHub returned {error.code} for {owner}/{repo}')


def _quote(ref: str) -> str:
    return urllib.parse.quote(ref, safe='')

"""The real HTTP path, against a local server.

`StubSource` covers everything above the network. These tests exist for the
layer it skips: request headers, status handling, and the JSON shapes GitHub
actually returns. goselfupdate does the same thing with httptest.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer

import pytest

from pyselfupdate.config import Config
from pyselfupdate.errors import NoReleaseError
from pyselfupdate.errors import SourceError
from pyselfupdate.github import GitHubSource
from pyselfupdate.github import token_from_env


class Recorder:
    """Routes and the requests they received."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, object]] = {}
        self.requests: list[tuple[str, dict[str, str]]] = []


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[Recorder]:
    recorder = Recorder()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
            # Lower-cased because HTTP header names are case-insensitive and
            # urllib does not preserve the casing they were added with.
            recorder.requests.append((self.path, {name.lower(): value for name, value in self.headers.items()}))
            status, payload = recorder.routes.get(self.path, (404, {'message': 'Not Found'}))
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            if status == 403:
                self.send_header('x-ratelimit-remaining', '0')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return

    httpd = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr('pyselfupdate.github.API', f'http://127.0.0.1:{httpd.server_port}')
    try:
        yield recorder
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def source(**kwargs) -> GitHubSource:
    return GitHubSource(owner='datapointchris', repo='demo', timeout=5, **kwargs)


def test_reads_the_latest_release(server: Recorder) -> None:
    server.routes['/repos/datapointchris/demo/releases/latest'] = (
        200,
        {'tag_name': 'v1.4.0', 'html_url': 'https://example.invalid/v1.4.0', 'body': 'notes'},
    )

    release = source().latest_release()

    assert release.tag == 'v1.4.0'
    assert release.install_ref() == 'v1.4.0'
    assert release.url == 'https://example.invalid/v1.4.0'


def test_sends_the_documented_api_headers(server: Recorder) -> None:
    server.routes['/repos/datapointchris/demo/releases/latest'] = (200, {'tag_name': 'v1.0.0'})

    source().latest_release()

    _, headers = server.requests[0]
    assert headers['accept'] == 'application/vnd.github+json'
    assert headers['x-github-api-version'] == '2022-11-28'
    # No header here only because the autouse fixture empties the token command.
    # The default is authenticated; `test_the_token_command_runs_by_default`
    # is what holds that.
    assert 'authorization' not in headers


def test_sends_a_token_when_one_is_configured(server: Recorder) -> None:
    server.routes['/repos/datapointchris/demo/releases/latest'] = (200, {'tag_name': 'v1.0.0'})

    source(token='secret').latest_release()

    assert server.requests[0][1]['authorization'] == 'Bearer secret'


def test_reads_a_token_from_the_environment(server: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('GH_TOKEN', 'from-env')
    server.routes['/repos/datapointchris/demo/releases/latest'] = (200, {'tag_name': 'v1.0.0'})

    source().latest_release()

    assert server.requests[0][1]['authorization'] == 'Bearer from-env'


def test_token_func_supplies_a_token_when_nothing_else_does(server: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    monkeypatch.delenv('GH_TOKEN', raising=False)
    server.routes['/repos/datapointchris/demo/releases/latest'] = (200, {'tag_name': 'v1.0.0'})

    source(token_func=lambda: 'from-func').latest_release()

    assert server.requests[0][1]['authorization'] == 'Bearer from-func'


@pytest.mark.parametrize(
    ('token', 'environment'),
    [
        ('explicit', {}),
        ('', {'GH_TOKEN': 'from-env'}),
        ('', {'GITHUB_TOKEN': 'from-env'}),
    ],
)
def test_token_func_is_skipped_when_a_cheaper_source_answered(
    server: Recorder, monkeypatch: pytest.MonkeyPatch, token: str, environment: dict[str, str]
) -> None:
    """A caller that already has a credential must never pay for the expensive one.

    Not "the last resort" any more: `token_from_command` runs after this, so what
    the test holds is the skip rather than the position.
    """
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    monkeypatch.delenv('GH_TOKEN', raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    server.routes['/repos/datapointchris/demo/releases/latest'] = (200, {'tag_name': 'v1.0.0'})

    calls = 0

    def expensive() -> str:
        nonlocal calls
        calls += 1
        return 'from-func'

    source(token=token, token_func=expensive).latest_release()

    assert calls == 0
    assert server.requests[0][1]['authorization'] != 'Bearer from-func'


def test_building_a_config_does_not_call_token_func() -> None:
    """The point of token_func is that it is not called until a request is made.

    The notify gate resolves a Config on every invocation and declines most of
    them without reaching the network; a subprocess in front of that gate is the
    entire cost the field exists to avoid.
    """
    calls = 0

    def expensive() -> str:
        nonlocal calls
        calls += 1
        return ''

    Config(tool='demo', owner='datapointchris', version='1.0.0', token_func=expensive).resolved()

    assert calls == 0


def test_github_token_outranks_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('GITHUB_TOKEN', 'first')
    monkeypatch.setenv('GH_TOKEN', 'second')
    assert token_from_env() == 'first'


def test_a_prefixed_tag_lists_and_filters(server: Recorder) -> None:
    """`/releases/latest` returns whichever release is newest overall.

    A repository whose app owns the bare v* tags and whose CLI uses `cli/v*`
    needs the list endpoint or it gets the app's release.
    """
    server.routes['/repos/datapointchris/demo/releases?per_page=100'] = (
        200,
        [
            {'tag_name': 'v9.9.9', 'draft': False, 'prerelease': False},
            {'tag_name': 'cli/v0.3.3', 'draft': False, 'prerelease': False},
            {'tag_name': 'cli/v0.3.2', 'draft': False, 'prerelease': False},
        ],
    )

    release = source(tag_prefix='cli/').latest_release()

    assert release.tag == 'v0.3.3', 'the prefix is stripped from the reported version'
    assert release.install_ref() == 'cli/v0.3.3', 'but git needs it spelled exactly'


def test_drafts_and_prereleases_are_skipped_by_default(server: Recorder) -> None:
    server.routes['/repos/datapointchris/demo/releases?per_page=100'] = (
        200,
        [
            {'tag_name': 'cli/v2.0.0', 'draft': True, 'prerelease': False},
            {'tag_name': 'cli/v1.9.0', 'draft': False, 'prerelease': True},
            {'tag_name': 'cli/v1.8.0', 'draft': False, 'prerelease': False},
        ],
    )

    assert source(tag_prefix='cli/').latest_release().tag == 'v1.8.0'


def test_prereleases_are_included_when_asked_for(server: Recorder) -> None:
    server.routes['/repos/datapointchris/demo/releases?per_page=100'] = (
        200,
        [
            {'tag_name': 'v1.9.0', 'draft': False, 'prerelease': True},
            {'tag_name': 'v1.8.0', 'draft': False, 'prerelease': False},
        ],
    )

    assert source(allow_prerelease=True).latest_release().tag == 'v1.9.0'


def test_a_404_reads_as_no_release_or_private(server: Recorder) -> None:
    with pytest.raises(NoReleaseError, match='private'):
        source().latest_release()


def test_an_exhausted_rate_limit_says_so(server: Recorder) -> None:
    server.routes['/repos/datapointchris/demo/releases/latest'] = (403, {'message': 'rate limit'})

    with pytest.raises(SourceError, match='rate limit'):
        source().latest_release()


def test_no_matching_prefix_is_no_release(server: Recorder) -> None:
    server.routes['/repos/datapointchris/demo/releases?per_page=100'] = (
        200,
        [{'tag_name': 'v1.0.0', 'draft': False, 'prerelease': False}],
    )

    with pytest.raises(NoReleaseError, match='cli/'):
        source(tag_prefix='cli/').latest_release()


def test_changelog_returns_commit_subjects(server: Recorder) -> None:
    server.routes['/repos/datapointchris/demo/compare/v1.0.0...v1.1.0'] = (
        200,
        {
            'commits': [
                {'commit': {'message': 'feat: add a thing\n\nwith a body that is not the subject'}},
                {'commit': {'message': 'fix: correct the thing'}},
            ]
        },
    )

    assert source().changelog('v1.0.0', 'v1.1.0') == ['feat: add a thing', 'fix: correct the thing']


def test_changelog_swallows_failures(server: Recorder) -> None:
    """A missing changelog must never fail an update that already succeeded."""
    assert source().changelog('v1.0.0', 'v1.1.0') == []


def test_changelog_is_empty_between_identical_refs(server: Recorder) -> None:
    assert source().changelog('v1.0.0', 'v1.0.0') == []
    assert not server.requests, 'and does not spend a request finding that out'


def test_an_unreachable_host_is_a_source_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Port 1 on the loopback interface has nothing listening on it.
    monkeypatch.setattr('pyselfupdate.github.API', 'http://127.0.0.1:1')

    with pytest.raises(SourceError, match='cannot reach'):
        source().latest_release()


def test_a_non_http_scheme_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """urlopen reads file: URLs as happily as http:, so the scheme is checked.

    API is a module attribute a caller can reassign, which is what makes this
    reachable at all rather than theoretical.
    """
    monkeypatch.setattr('pyselfupdate.github.API', 'file:///etc/passwd#')

    with pytest.raises(SourceError, match='only http and https'):
        source().latest_release()


def printing(token: str) -> str:
    """A token command that succeeds, spelled portably.

    `printf` rather than `echo` because `shutil.which` has to find it on PATH
    and the shell builtin is not a file anywhere.
    """
    return f'printf {token}'


def test_the_token_command_runs_by_default(server: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    """Authenticating is the default. The alternative is not "no credential" but
    sixty requests an hour charged per IP address."""
    server.routes['/repos/datapointchris/demo/releases/latest'] = (200, {'tag_name': 'v1.0.0'})
    monkeypatch.setenv('GITHUB_TOKEN_COMMAND', printing('from-command'))

    source().latest_release()

    assert server.requests[0][1]['authorization'] == 'Bearer from-command'


def test_an_empty_token_command_disables_authentication(server: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    """The lever has to turn the thing off as well as redirect it, or it is not
    a switch and the default may as well be hardcoded."""
    server.routes['/repos/datapointchris/demo/releases/latest'] = (200, {'tag_name': 'v1.0.0'})
    monkeypatch.setenv('GITHUB_TOKEN_COMMAND', '')

    source().latest_release()

    assert 'authorization' not in server.requests[0][1]


def test_a_failing_token_command_degrades_to_anonymous(server: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    """A vault that is locked, or a helper that is not installed, must not fail
    the update check. Anonymous still works against a public repository."""
    server.routes['/repos/datapointchris/demo/releases/latest'] = (200, {'tag_name': 'v1.0.0'})
    monkeypatch.setenv('GITHUB_TOKEN_COMMAND', 'false')

    source().latest_release()

    assert 'authorization' not in server.requests[0][1]


def test_a_token_command_that_is_not_installed_degrades_to_anonymous(server: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    server.routes['/repos/datapointchris/demo/releases/latest'] = (200, {'tag_name': 'v1.0.0'})
    monkeypatch.setenv('GITHUB_TOKEN_COMMAND', 'no-such-binary-anywhere --token')

    source().latest_release()

    assert 'authorization' not in server.requests[0][1]


def test_the_environment_beats_the_command(server: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    """A user who exported a token meant that one, and it costs no subprocess."""
    server.routes['/repos/datapointchris/demo/releases/latest'] = (200, {'tag_name': 'v1.0.0'})
    monkeypatch.setenv('GITHUB_TOKEN', 'from-env')
    monkeypatch.setenv('GITHUB_TOKEN_COMMAND', printing('from-command'))

    source().latest_release()

    assert server.requests[0][1]['authorization'] == 'Bearer from-env'


def test_a_callers_own_source_beats_the_command(server: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    """`token_func` is now for a credential neither the environment nor a command
    can produce, so it has to win over the default."""
    server.routes['/repos/datapointchris/demo/releases/latest'] = (200, {'tag_name': 'v1.0.0'})
    monkeypatch.setenv('GITHUB_TOKEN_COMMAND', printing('from-command'))

    source(token_func=lambda: 'from-func').latest_release()

    assert server.requests[0][1]['authorization'] == 'Bearer from-func'


def test_the_command_runs_once_per_source(server: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    """A check that also fetches a changelog makes several requests, and the
    command behind this can be a vault unlock with a touch prompt."""
    server.routes['/repos/datapointchris/demo/releases/latest'] = (200, {'tag_name': 'v1.0.0'})
    server.routes['/repos/datapointchris/demo/compare/v1.0.0...v1.1.0'] = (200, {'commits': []})
    calls = 0

    def counted() -> str:
        nonlocal calls
        calls += 1
        return 'tok'

    monkeypatch.setattr('pyselfupdate.github.token_from_command', counted)

    built = source()
    built.latest_release()
    built.changelog('v1.0.0', 'v1.1.0')

    assert calls == 1


def test_an_anonymous_rate_limit_names_the_anonymous_ceiling(server: Recorder) -> None:
    """The two ceilings are different problems with different fixes. One
    sentence for both misadvised whichever case it was not written for."""
    server.routes['/repos/datapointchris/demo/releases/latest'] = (403, {'message': 'rate limit'})

    with pytest.raises(SourceError, match='anonymous rate limit'):
        source().latest_release()


def test_an_authenticated_rate_limit_does_not_advise_supplying_a_token(server: Recorder) -> None:
    """Telling someone to set a token when the request already carried one reads
    as advice to configure something that is already configured."""
    server.routes['/repos/datapointchris/demo/releases/latest'] = (403, {'message': 'rate limit'})

    with pytest.raises(SourceError) as raised:
        source(token='secret').latest_release()

    assert 'authenticated rate limit' in str(raised.value)
    assert 'GITHUB_TOKEN' not in str(raised.value)

"""Shared fixtures.

Everything here runs offline. `StubSource` serves fixed releases the way
goselfupdate's `stubSource` does; the real HTTP path is exercised separately in
test_github.py against a local http.server.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import pytest

from pyselfupdate.errors import NoReleaseError
from pyselfupdate.source import Release

# Every variable that can change what the gate decides. Cleared before each test
# so a developer's own shell -- CI=1 in a terminal, an exported NO_AUTO_UPDATE --
# cannot make the suite pass or fail for the wrong reason.
GATE_VARIABLES = (
    'NO_AUTO_UPDATE',
    'AUTO_UPDATE_INTERVAL',
    'CI',
    'BUILD_NUMBER',
    'RUN_ID',
    'GITHUB_ACTIONS',
    'CODESPACES',
    'GITHUB_TOKEN',
    'GH_TOKEN',
)


@dataclass
class StubSource:
    """A source serving a fixed release, counting how often it was asked."""

    tag: str = 'v2.0.0'
    ref: str = ''
    error: Exception | None = None
    calls: int = 0
    subjects: list[str] = field(default_factory=list)

    def latest_release(self) -> Release:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if not self.tag:
            raise NoReleaseError('stub publishes no release')
        return Release(tag=self.tag, ref=self.ref or self.tag, url=f'https://example.invalid/{self.tag}')

    def changelog(self, from_ref: str, to_ref: str) -> list[str]:
        return self.subjects.copy()


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in GATE_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / 'state'
    monkeypatch.setenv('XDG_STATE_HOME', str(home))
    return home


@pytest.fixture
def uv_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tools = tmp_path / 'uv-tools'
    tools.mkdir()
    monkeypatch.setenv('UV_TOOL_DIR', str(tools))
    return tools


@pytest.fixture
def make_receipt(uv_tools: Path):
    """Writes a uv receipt of a given shape and returns the tool directory."""

    def write(tool: str, requirement: str) -> Path:
        directory = uv_tools / tool
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'uv-receipt.toml').write_text(
            f'[tool]\nrequirements = [{requirement}]\nentrypoints = []\n',
            encoding='utf-8',
        )
        return directory

    return write


class FakeStream:
    """A writable stream that records what was written and reports its own tty-ness.

    Terminal detection is injected rather than faked by reassigning sys.stdout:
    pytest's capture reinstalls its own streams between fixture setup and the
    test call, so a monkeypatched sys.stdout silently reverts and every gate
    test would pass for the wrong reason.
    """

    def __init__(self, *, tty: bool = True) -> None:
        self.lines: list[str] = []
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty

    def write(self, text: str) -> int:
        self.lines.append(text)
        return len(text)

    def flush(self) -> None:
        return

    @property
    def text(self) -> str:
        return ''.join(self.lines)


@pytest.fixture
def out() -> FakeStream:
    """The stream a notice is written to."""
    return FakeStream()


def written_state(state_home: Path, tool: str) -> dict:
    return json.loads((state_home / tool / 'autoupdate.json').read_text(encoding='utf-8'))


def environ_without(*names: str) -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in names}

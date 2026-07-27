"""What to update, and how to reach it."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from pyselfupdate.errors import InvalidConfigError
from pyselfupdate.github import GitHubSource
from pyselfupdate.install import current_version
from pyselfupdate.source import Source

DEFAULT_TIMEOUT = 10.0


@dataclass
class Config:
    """Describes one updatable tool.

    `tool` is the only required field. Everything else either has a working
    default or is derived from it, so the common case is `Config(tool='syncer',
    owner='datapointchris')`.
    """

    # The uv tool name. Names the receipt directory, the entry point, and the
    # state file, and is what appears in messages.
    tool: str

    owner: str = ''
    repo: str = ''

    # The distribution name to read the running version from, when it differs
    # from the tool name. Defaults to `tool`.
    package: str = ''

    # The running version. Defaults to the installed distribution's metadata,
    # which is correct for anything installed as a uv tool.
    version: str = ''

    token: str = ''
    timeout: float = DEFAULT_TIMEOUT
    allow_prerelease: bool = False

    # Selects one release stream in a repository publishing several, as in
    # "cli/" for tags of the form cli/v1.2.3. Configures the default GitHub
    # source and is unused when `source` is supplied.
    tag_prefix: str = ''

    # Locates releases. Defaults to a GitHubSource built from the fields above.
    source: Source | None = None

    metadata: dict[str, str] = field(default_factory=dict)

    def require_source(self) -> Source:
        """The resolved source.

        `resolved()` always populates `source`, but the field stays optional so
        that constructing a Config does not require one. This is the accessor
        that expresses "past this point it is set", rather than each caller
        asserting it.
        """
        if self.source is None:
            raise InvalidConfigError('config was not resolved before use')
        return self.source

    def resolved(self) -> Config:
        """A copy with every default filled in, so callers can assume they are set."""
        if not self.tool:
            raise InvalidConfigError('tool is required')

        source = self.source
        owner = self.owner
        repo = self.repo or self.tool

        if source is None:
            if not owner:
                raise InvalidConfigError('owner is required without a custom source')
            source = GitHubSource(
                owner=owner,
                repo=repo,
                token=self.token,
                timeout=self.timeout,
                allow_prerelease=self.allow_prerelease,
                tag_prefix=self.tag_prefix,
            )

        package = self.package or self.tool
        return Config(
            tool=self.tool,
            owner=owner,
            repo=repo,
            package=package,
            version=self.version or current_version(package),
            token=self.token,
            timeout=self.timeout,
            allow_prerelease=self.allow_prerelease,
            tag_prefix=self.tag_prefix,
            source=source,
            metadata=self.metadata.copy(),
        )

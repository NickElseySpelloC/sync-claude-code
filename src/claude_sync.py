"""Thin wrapper around the ``claude-sync`` command-line tool."""

import shutil
import subprocess  # noqa: S404
from pathlib import Path

import yaml


class ClaudeSyncError(RuntimeError):
    """Raised when claude-sync is missing or a command fails."""


class ClaudeSync:
    """Runs claude-sync commands, streaming their output to the console."""

    def __init__(self, binary: str = "claude-sync", config_file: Path | None = None) -> None:
        """Set up the wrapper.

        Args:
            binary: Name or path of the claude-sync executable.
            config_file: claude-sync's own config file, used to report its scope.
        """
        self.binary = binary
        self.config_file = config_file or Path.home() / ".claude-sync" / "config.yaml"

    def _resolve(self) -> str:
        """Find the claude-sync executable.

        Returns:
            The absolute path to the executable.

        Raises:
            ClaudeSyncError: If it can't be found.
        """
        found = shutil.which(self.binary)
        if found is None:
            msg = f"claude-sync not found (looked for '{self.binary}'). Install it or set ClaudeSync.Binary in the config."
            raise ClaudeSyncError(msg)
        return found

    def run(self, *args: str) -> None:
        """Run a claude-sync command, letting it use the terminal directly.

        Output is not captured, so its progress and any prompts reach the user.

        Args:
            *args: Arguments for claude-sync, e.g. ``"push"``.

        Raises:
            ClaudeSyncError: If the binary is missing or exits non-zero.
        """
        cmd = [self._resolve(), *args]
        result = subprocess.run(cmd, check=False)  # noqa: S603
        if result.returncode != 0:
            msg = f"'claude-sync {' '.join(args)}' failed with exit code {result.returncode}"
            raise ClaudeSyncError(msg)

    def push(self) -> None:
        """Run ``claude-sync push``."""
        self.run("push")

    def pull(self) -> None:
        """Run ``claude-sync pull``."""
        self.run("pull")

    def resolve_conflicts_remote(self) -> None:
        """Run ``claude-sync conflicts --keep remote``."""
        self.run("conflicts", "--keep", "remote")

    def version(self) -> str | None:
        """Report claude-sync's version.

        Returns:
            The version string, or None if claude-sync isn't available.
        """
        try:
            cmd = [self._resolve(), "--version"]
        except ClaudeSyncError:
            return None
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
        if result.returncode != 0:
            return None
        return result.stdout.strip().removeprefix("claude-sync version ").strip()

    def scope(self) -> str | None:
        """Read the sync scope from claude-sync's config file.

        Returns:
            The scope (``"full"`` when unset), or None if the config can't be read.
        """
        try:
            data = yaml.safe_load(self.config_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None
        if not isinstance(data, dict):
            return None
        scope = data.get("scope")
        return str(scope) if scope else "full"

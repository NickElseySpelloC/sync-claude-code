"""Configuration schema for use with SCConfigManager."""

from typing import Any

_STR = {"type": "string", "required": False, "nullable": True}
_BOOL = {"type": "boolean", "required": False, "nullable": True}


class ConfigSchema:
    """Cerberus validation schema for the app's YAML config."""

    def __init__(self) -> None:
        """Build the schema."""
        self.validation: dict[str, Any] = {
            "General": {
                "type": "dict",
                "required": False,
                "nullable": True,
                "schema": {"AppName": _STR},
            },
            "Paths": {
                "type": "dict",
                "required": False,
                "nullable": True,
                "schema": {
                    "ClaudeDir": _STR,
                    "AppSupportDir": _STR,
                    "StagingDir": _STR,
                    "BackupDir": _STR,
                },
            },
            "ClaudeSync": {
                "type": "dict",
                "required": False,
                "nullable": True,
                "schema": {
                    "Binary": _STR,
                    "RunSync": _BOOL,
                    "AutoResolveConflicts": _BOOL,
                },
            },
            "Pull": {
                "type": "dict",
                "required": False,
                "nullable": True,
                "schema": {
                    "Mirror": _BOOL,
                    "SyncSidebar": _BOOL,
                },
            },
        }

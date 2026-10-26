"""Opt-in reporting facts; source-only API pilot is separate from game/UI."""

SCHEMA_VERSION = "press.weekly/0.1-draft"
EXPORTER_VERSION = "weekly-export/4"
POLICY_VERSION = "weekly-editorial/4"


class PressError(ValueError):
    pass

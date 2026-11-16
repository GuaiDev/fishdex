"""Base jurisdiction — a region the bot can reason about."""

from dataclasses import dataclass


@dataclass(kw_only=True)
class Jurisdiction:
    code: str
    name: str
    country: str
    has_detailed_data: bool = False
    context: str | None = None
    """Region-specific prompt text, from the plugin's `regulatory_context`."""

    def regulatory_context(self) -> str:
        if self.context:
            return self.context
        return (
            f"## {self.name} ({self.code})\n\n"
            f"I don't have detailed regulatory or stocking data populated for "
            f"{self.name} yet. Please verify current rules with the relevant "
            f"fish & wildlife agency before fishing."
        )

"""Small, hardware-independent backend contract shared by all implementations."""
from collections.abc import Sequence
from typing import Protocol


class BackendUnavailable(Exception):
    """The backend cannot establish a trustworthy observation or effect."""


class Backend(Protocol):
    info: dict

    async def snapshot(self, fields: Sequence[str]) -> dict:
        """Return only requested observations; never invent missing values."""
        ...

    async def apply(self, action: str, value: object) -> None:
        """Apply one explicit action or raise; acknowledgment alone is insufficient."""
        ...

    async def close(self) -> None:
        """Release resources; never imply verified physical disconnection."""
        ...

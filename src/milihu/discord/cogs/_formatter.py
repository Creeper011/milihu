"""Message formatting helpers for Discord cogs using Components v2."""

from typing import Self

from discord.ui import Container, LayoutView, TextDisplay


class CogMessageFormatter:
    """Singleton formatter for user-facing cog Components v2 views."""

    _instance: Self | None = None

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def error_view(self, title: str, message: str) -> LayoutView:
        """Build a Components v2 error ``LayoutView``."""
        view = LayoutView()
        view.add_item(
            Container[LayoutView](
                TextDisplay[LayoutView](f"### {title}"),
                TextDisplay[LayoutView](message),
            )
        )
        return view

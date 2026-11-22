"""união flasco"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast, override

from discord import Intents, app_commands
from discord.ext import commands
from discord.gateway import DiscordWebSocket
from discord.utils import MISSING
from pydantic import validate_call

from milihu.discord.cogfinder import CogFinder
from milihu.types.cogpath import CogPath
from milihu.types.default.prefix import Prefix
from milihu.types.token import Token

_MOBILE_BROWSER = "Discord iOS"
_mobile_presence_patched = False


def _enable_mobile_presence() -> None:
    """Patch ``DiscordWebSocket.send_as_json`` so IDENTIFY reports a mobile client."""
    # shortcut: patches IDENTIFY browser for mobile icon; drop if discord.py exposes it
    global _mobile_presence_patched
    if _mobile_presence_patched:
        return

    original_send_as_json = DiscordWebSocket.send_as_json

    async def send_as_json_mobile(self: DiscordWebSocket, data: Any) -> None:
        if isinstance(data, dict):
            packet = cast(dict[str, Any], data)
            payload = packet.get("d")
            if packet.get("op") == self.IDENTIFY and isinstance(payload, dict):
                properties = cast(dict[str, Any], payload).get("properties")
                if isinstance(properties, dict):
                    cast(dict[str, Any], properties)["browser"] = _MOBILE_BROWSER
        await original_send_as_json(self, data)

    DiscordWebSocket.send_as_json = send_as_json_mobile  # type: ignore[method-assign]
    _mobile_presence_patched = True


class MilihuBaseBot(commands.AutoShardedBot):
    """Defines the Milihu Bot Client."""

    @validate_call(config={"arbitrary_types_allowed": True})
    def __init__(
        self,
        command_prefix: Prefix | None = None,
        *,
        intents: Intents | None = None,
        **options: Any,
    ) -> None:
        if command_prefix is None:
            raise ValueError("command prefix is required")

        _enable_mobile_presence()
        options.setdefault(
            "allowed_installs",
            app_commands.AppInstallationType(guild=True, user=True),
        )
        options.setdefault(
            "allowed_contexts",
            app_commands.AppCommandContext(
                guild=True,
                dm_channel=True,
                private_channel=True,
            ),
        )
        options.setdefault("help_command", None)
        super().__init__(
            command_prefix=command_prefix,
            intents=intents or Intents.default(),
            **options,
        )

    @override
    async def setup_hook(self) -> None:
        """Synchronize application commands with Discord on login."""
        await self.tree.sync()

    async def load_cogs(
        self,
        cog_finder: CogFinder,
        loader: Callable[[CogPath], Awaitable[None]],
    ) -> None:
        """Load all cog paths discovered by ``cog_finder`` using ``loader``."""
        for cog_path in cog_finder:
            await loader(cog_path)

    @override
    @validate_call(config={"arbitrary_types_allowed": True})
    def run(
        self,
        token: Token,
        *,
        reconnect: bool = True,
        log_handler: logging.Handler | None = MISSING,
        log_formatter: logging.Formatter = MISSING,
        log_level: int = MISSING,
        root_logger: bool = False,
    ) -> None:
        """Start the bot using the provided Discord token."""
        super().run(
            token,
            reconnect=reconnect,
            log_handler=log_handler,
            log_formatter=log_formatter,
            log_level=log_level,
            root_logger=root_logger,
        )

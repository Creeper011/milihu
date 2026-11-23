import json
import unittest
from typing import Any
from unittest.mock import AsyncMock

from discord import ActivityType, Intents, Status
from discord.gateway import DiscordWebSocket

from milihu.app.app import MilihuApp
from milihu.app.registry import build_cog_registry
from milihu.discord.basebot import MilihuBaseBot
from milihu.discord.cogfinder import CogFinder
from milihu.discord.loader import CogLoader
from milihu.types.default.cogs import DEFAULT_COGS_DIR, DEFAULT_COGS_PACKAGE


class BaseBotPresenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_mobile_presence_patch_rewrites_identify_browser(self) -> None:
        MilihuBaseBot(command_prefix="m$", intents=Intents.default())
        patched_method = DiscordWebSocket.send_as_json
        MilihuBaseBot(command_prefix="m$", intents=Intents.default())
        self.assertIs(DiscordWebSocket.send_as_json, patched_method)

        ws = DiscordWebSocket.__new__(DiscordWebSocket)
        sent: list[dict[str, Any]] = []

        async def fake_send(raw: str) -> None:
            sent.append(json.loads(raw))

        ws.send = AsyncMock(side_effect=fake_send)

        await ws.send_as_json(
            {
                "op": DiscordWebSocket.IDENTIFY,
                "d": {
                    "token": "test",
                    "properties": {
                        "os": "linux",
                        "browser": "discord.py",
                        "device": "discord.py",
                    },
                },
            }
        )
        await ws.send_as_json({"op": DiscordWebSocket.HEARTBEAT, "d": 1})

        self.assertEqual(sent[0]["d"]["properties"]["browser"], "Discord iOS")
        self.assertEqual(sent[1], {"op": DiscordWebSocket.HEARTBEAT, "d": 1})

    def test_app_builds_bot_with_online_custom_status(self) -> None:
        app = MilihuApp(token="test")

        state: Any = app.bot._connection  # pyright: ignore[reportPrivateUsage]
        self.assertEqual(state._status, str(Status.online))
        self.assertEqual(
            state._activity,
            {
                "type": ActivityType.custom.value,
                "state": "🌙 yeaa, idk what i'm doing",
                "name": "Custom Status",
            },
        )

    async def test_commands_are_user_installable_except_guild_only_music(self) -> None:
        bot = MilihuBaseBot(command_prefix="m$", intents=Intents.default())
        finder = CogFinder(DEFAULT_COGS_DIR, package=DEFAULT_COGS_PACKAGE)
        loader = CogLoader(bot, finder, build_cog_registry(None))
        await bot.load_cogs(finder, loader.load)

        payloads = {cmd.name: cmd.to_dict(bot.tree) for cmd in bot.tree.get_commands()}
        self.assertEqual(
            set(payloads),
            {
                "about",
                "download",
                "gif",
                "caption",
                "op",
                "help",
                "music",
                "To GIF",
                "Caption",
            },
        )
        for name in (
            "about",
            "download",
            "gif",
            "caption",
            "op",
            "help",
            "To GIF",
            "Caption",
        ):
            self.assertEqual(payloads[name]["integration_types"], [0, 1], name)
            self.assertEqual(payloads[name]["contexts"], [0, 1, 2], name)
        self.assertEqual(payloads["music"]["integration_types"], [0])
        self.assertEqual(payloads["music"]["contexts"], [0])

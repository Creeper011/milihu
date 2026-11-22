import unittest
from types import SimpleNamespace
from typing import Any, cast

from discord import Intents
from discord.ext import commands

from milihu.app.registry import build_cog_registry
from milihu.discord.basebot import MilihuBaseBot
from milihu.discord.cogfinder import CogFinder
from milihu.discord.cogs.eval import EvalCog
from milihu.discord.loader import CogLoader
from milihu.discord.types.bot import BotClient
from milihu.types.default.cogs import DEFAULT_COGS_DIR, DEFAULT_COGS_PACKAGE


class Context:
    def __init__(self, user_id: int = 713544201828040786) -> None:
        self.author = SimpleNamespace(id=user_id)
        self.bot = object()
        self.replies: list[dict[str, Any]] = []

    async def reply(self, **kwargs: Any) -> None:
        self.replies.append(kwargs)


class EvalCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_denied_check_is_logged_without_reply_or_reraise(self) -> None:
        cog = EvalCog(cast(BotClient, object()))
        ctx = cast(Any, Context(user_id=123))
        with self.assertLogs("milihu.discord.cogs.eval", level="WARNING") as logs:
            await cog.cog_command_error(ctx, commands.CheckFailure("denied"))
        self.assertIn("123", logs.output[0])
        self.assertEqual([], ctx.replies)

    async def test_discovered_as_prefix_command_only(self) -> None:
        bot = MilihuBaseBot(command_prefix="m$", intents=Intents.default())
        finder = CogFinder(DEFAULT_COGS_DIR, package=DEFAULT_COGS_PACKAGE)
        loader = CogLoader(bot, finder, build_cog_registry(None))
        await loader.load_all()
        self.assertIsNotNone(bot.get_command("ev"))
        self.assertNotIn("ev", {command.name for command in bot.tree.get_commands()})

    async def test_authorization_and_execution(self) -> None:
        command = cast(Any, EvalCog.eval_code)
        allowed = cast(Any, Context())
        denied = cast(Any, Context(user_id=1))
        self.assertTrue(command.checks[0](allowed))
        self.assertFalse(command.checks[0](denied))

        cog = EvalCog(cast(BotClient, object()))
        await command.callback(
            cog,
            allowed,
            code=(
                "```py\nprint('hello')\n"
                "await __import__('asyncio').sleep(0)\n1 + 1\n```"
            ),
        )
        self.assertIn("hello", allowed.replies[0]["content"])
        self.assertIn("2", allowed.replies[0]["content"])

    async def test_return_error_and_long_output(self) -> None:
        cog = EvalCog(cast(BotClient, object()))
        command = cast(Any, EvalCog.eval_code)
        ctx = cast(Any, Context())
        await command.callback(cog, ctx, code="return 42")
        self.assertEqual("42", ctx.replies[-1]["content"])

        await command.callback(cog, ctx, code="raise ValueError('boom')")
        self.assertIn("ValueError: boom", ctx.replies[-1]["content"])

        await command.callback(cog, ctx, code="print('x' * 3000)")
        self.assertIn("file", ctx.replies[-1])


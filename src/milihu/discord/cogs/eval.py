"""Restricted, in-process Python evaluation for bot debugging."""

import ast
import io
import logging
import traceback
from collections.abc import Awaitable, Callable
from typing import Any, cast, override

from discord import AllowedMentions, File
from discord.ext import commands

from milihu.discord.types.bot import BotClient
from milihu.types.default.eval import EVAL_USER_IDS
from milihu.types.eval import FENCED_PYTHON_CODE

_LOGGER = logging.getLogger(__name__)


class EvalCog(commands.Cog):
    def __init__(self, bot: BotClient) -> None:
        self.bot = bot

    @override
    async def cog_command_error(
        self, ctx: commands.Context[BotClient], error: Exception
    ) -> None:
        if isinstance(error, commands.CheckFailure):
            _LOGGER.warning("Denied m$ev for user %s", ctx.author.id)
            return
        raise error

    @commands.command(name="ev")
    @commands.check(lambda ctx: ctx.author.id in EVAL_USER_IDS)
    async def eval_code(self, ctx: commands.Context[BotClient], *, code: str) -> None:
        """Run Python with access to the command context and bot."""
        match = FENCED_PYTHON_CODE.fullmatch(code.strip())
        source = match.group("code") if match else code
        output = io.StringIO()

        def captured_print(*args: Any, **kwargs: Any) -> None:
            print(*args, file=output, **kwargs)

        try:
            tree = ast.parse(source)
            body = tree.body
            if body and isinstance(body[-1], ast.Expr):
                body[-1] = ast.Return(value=body[-1].value)
            function = ast.AsyncFunctionDef(
                name="_evaluate",
                args=ast.arguments(
                    posonlyargs=[], args=[], vararg=None, kwonlyargs=[],
                    kw_defaults=[], kwarg=None, defaults=[],
                ),
                body=body or [ast.Pass()],
                decorator_list=[],
            )
            module = ast.fix_missing_locations(
                ast.Module(body=[function], type_ignores=[])
            )
            namespace: dict[str, Any] = {
                "ctx": ctx, "bot": self.bot, "print": captured_print
            }
            exec(compile(module, "<m$ev>", "exec"), namespace)
            evaluate = cast(Callable[[], Awaitable[Any]], namespace["_evaluate"])
            result = await evaluate()
            if result is not None:
                output.write(repr(result))
        except Exception:
            output.write(traceback.format_exc())

        response = output.getvalue() or "ran without errors!! yeaa"
        if len(response) <= 2000:
            await ctx.reply(content=response, allowed_mentions=AllowedMentions.none())
        else:
            await ctx.reply(
                file=File(io.BytesIO(response.encode()), filename="eval.txt"),
                allowed_mentions=AllowedMentions.none(),
            )

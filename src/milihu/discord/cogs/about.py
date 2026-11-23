"""Bot information command using Discord Components v2."""

import math
from typing import cast

from discord import AllowedMentions
from discord.ext import commands
from discord.ui import Container, LayoutView, Section, Separator, TextDisplay, Thumbnail

from milihu.discord.types.bot import BotClient
from milihu.faces.project import ProjectInfoProvider
from milihu.types.default.cogs import ACCENT_COLOUR
from milihu.types.default.prefix import DEFAULT_PREFIX
from milihu.types.default.project import CREATOR_USER_ID
from milihu.types.project import ProjectOutput


def _build_about_view(
    bot: BotClient,
    prefix: str,
    project: ProjectOutput,
) -> LayoutView:
    header_texts: list[TextDisplay[LayoutView]] = [
        TextDisplay[LayoutView]("### 🌙 About Milihu")
    ]
    if project.description is not None:
        header_texts.append(TextDisplay[LayoutView](project.description))

    latency = (
        f"{round(bot.latency * 1000)} ms" if math.isfinite(bot.latency) else "N/A"
    )
    detail_lines = [
        f"**Created by:** <@{CREATOR_USER_ID}>",
        f"**Servers:** {len(bot.guilds)} · **Ping:** {latency}",
    ]
    if project.stack:
        detail_lines.append(f"**Stack:** {' · '.join(project.stack)}")
    details = TextDisplay[LayoutView]("\n".join(detail_lines))
    footer = TextDisplay[LayoutView](
        f"-# Use `/help` or `{prefix}help` to browse commands."
    )

    header: tuple[Section[LayoutView], ...] | tuple[TextDisplay[LayoutView], ...]
    if bot.user is not None:
        header = (
            Section[LayoutView](
                *header_texts,
                accessory=Thumbnail[LayoutView](bot.user.display_avatar.url),
            ),
        )
    else:
        header = tuple(header_texts)

    return LayoutView().add_item(
        Container[LayoutView](
            *header,
            details,
            Separator[LayoutView](),
            footer,
            accent_colour=ACCENT_COLOUR,
        )
    )


class AboutCog(commands.Cog):
    """Displays general information about Milihu."""

    def __init__(self, bot: BotClient, project: ProjectInfoProvider) -> None:
        self.bot = bot
        self.project = project

    @commands.hybrid_command(name="about", aliases=["ab"])
    async def about(self, ctx: commands.Context[BotClient]) -> None:
        """Show information about Milihu."""
        prefix = cast(object, self.bot.command_prefix)
        project = await self.project.get_project()
        await ctx.reply(
            view=_build_about_view(
                self.bot,
                prefix if isinstance(prefix, str) else DEFAULT_PREFIX,
                project,
            ),
            allowed_mentions=AllowedMentions.none(),
        )

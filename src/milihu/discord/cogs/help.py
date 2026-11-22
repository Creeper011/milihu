"""Interactive command guide using Discord Components v2."""

from typing import Any, cast

from discord import Interaction, SelectOption
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Select, Separator, TextDisplay

from milihu.discord.types.bot import BotClient
from milihu.discord.types.help import HelpCategoryRegistry
from milihu.types.default.prefix import DEFAULT_PREFIX
from milihu.types.help import HelpCategoryKey, HelpPage


def _has_slash_command(command: commands.Command[None, ..., Any]) -> bool:
    return isinstance(command, (commands.HybridCommand, commands.HybridGroup))


def _command_in_cogs(
    command: commands.Command[None, ..., Any],
    cogs: tuple[type[commands.Cog], ...],
) -> bool:
    return isinstance(cast(object, command.cog), cogs)


def _parse_help_page(raw: str, categories: HelpCategoryRegistry) -> HelpPage:
    if raw == "home":
        return "home"
    try:
        key = HelpCategoryKey(raw)
    except ValueError:
        return "home"
    return key if key in categories else "home"


class _HelpView(LayoutView):
    def __init__(
        self,
        bot: BotClient,
        prefix: str,
        categories: HelpCategoryRegistry,
        page: HelpPage = "home",
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.prefix = prefix
        self.categories = categories

        selected_category = None if page == "home" else categories.get(page)
        active_page: HelpPage = "home" if selected_category is None else page

        select: Select[_HelpView] = Select(
            placeholder="Choose a category",
            options=[
                SelectOption(
                    label="Home",
                    value="home",
                    emoji="🏠",
                    default=active_page == "home",
                ),
                *(
                    SelectOption(
                        label=info.label,
                        value=key.value,
                        emoji=info.emoji,
                        default=active_page == key,
                    )
                    for key, info in categories.items()
                ),
            ],
        )
        select.callback = self._select_category

        if selected_category is None:
            body = "\n".join(
                f"{info.title} — browse commands in the menu below."
                for info in categories.values()
            )
            heading = "### 🌙 Milihu Help"
        else:
            heading = f"### {selected_category.title}"
            commands_in_category = sorted(
                (
                    command
                    for command in bot.walk_commands()
                    if _command_in_cogs(command, selected_category.items)
                    and not command.hidden
                ),
                key=lambda command: (
                    not _has_slash_command(command),
                    command.qualified_name,
                ),
            )
            body = "\n".join(
                self._command_line(command) for command in commands_in_category
            )
            if not body:
                body = "No commands available in this category."

        self.add_item(
            Container[_HelpView](
                TextDisplay[_HelpView](heading),
                TextDisplay[_HelpView](body),
                Separator[_HelpView](),
                TextDisplay[_HelpView](
                    f"-# Use `/help` or `{prefix}help` to open this help menu."
                ),
                ActionRow[_HelpView](select),
                accent_colour=0x5865F2,
            )
        )

    def _command_line(self, command: commands.Command[None, ..., Any]) -> str:
        name = command.qualified_name
        slash = f"`/{name}` · " if _has_slash_command(command) else ""
        description = command.short_doc or "No description."
        return f"**{slash}`{self.prefix}{name}`** — {description}"

    async def _select_category(self, interaction: Interaction[BotClient]) -> None:
        raw_selected = next(
            (
                item.values[0]
                for item in self.walk_children()
                if isinstance(item, Select) and item.values
            ),
            "home",
        )
        page = _parse_help_page(raw_selected, self.categories)
        await interaction.response.edit_message(
            view=_HelpView(self.bot, self.prefix, self.categories, page)
        )


class HelpCog(commands.Cog):
    """Displays the public command guide."""

    def __init__(self, bot: BotClient, categories: HelpCategoryRegistry) -> None:
        self.bot = bot
        self.categories = categories

    @commands.hybrid_command(name="help")
    async def help(self, ctx: commands.Context[BotClient]) -> None:
        """Show available commands and how to use them."""
        prefix = cast(object, self.bot.command_prefix)
        await ctx.reply(
            view=_HelpView(
                self.bot,
                prefix if isinstance(prefix, str) else DEFAULT_PREFIX,
                self.categories,
            )
        )

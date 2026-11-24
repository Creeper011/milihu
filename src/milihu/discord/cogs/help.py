"""Interactive command guide using Discord Components v2."""

import sys
from collections import defaultdict
from typing import Any, cast

from discord import Interaction, SelectOption
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Select, Separator, TextDisplay

from milihu.discord.types.bot import BotClient
from milihu.discord.types.help import CogHelpCategory, HelpCategoryRegistry
from milihu.types.default.cogs import ACCENT_COLOUR
from milihu.types.default.prefix import DEFAULT_PREFIX
from milihu.types.help import HelpCategoryKey, HelpPage


def _has_slash_command(
    command: commands.Command[None, ..., Any], bot: BotClient
) -> bool:
    return isinstance(command, (commands.HybridCommand, commands.HybridGroup)) or (
        bot.tree.get_command(command.qualified_name) is not None
    )


def _command_in_cogs(
    command: commands.Command[None, ..., Any],
    cogs: tuple[type[commands.Cog], ...],
) -> bool:
    return isinstance(cast(object, command.cog), cogs)


def _command_sort_key(
    command: commands.Command[None, ..., Any], bot: BotClient
) -> tuple[int, bool, str]:
    raw_order: object = command.extras.get("help_order")
    order = raw_order if isinstance(raw_order, int) else sys.maxsize
    return (order, not _has_slash_command(command, bot), command.qualified_name)


def _collect_category_commands(
    bot: BotClient,
    cogs: tuple[type[commands.Cog], ...],
) -> tuple[
    list[commands.Group[None, ..., Any]],
    list[commands.Command[None, ..., Any]],
    dict[str, list[str]],
]:
    visible: list[commands.Command[None, ..., Any]] = []
    shortcuts_by_target: dict[str, list[str]] = defaultdict(list)

    for command in bot.walk_commands():
        if not _command_in_cogs(command, cogs):
            continue
        if command.hidden:
            target: object = command.extras.get("help_shortcut_for")
            if isinstance(target, str):
                shortcuts_by_target[target].append(command.name)
            continue
        visible.append(command)

    parent_groups = {cmd.parent for cmd in visible if cmd.parent is not None}
    groups = sorted(
        (
            cmd
            for cmd in visible
            if isinstance(cmd, commands.Group) and cmd in parent_groups
        ),
        key=lambda cmd: _command_sort_key(cmd, bot),
    )
    commands_to_render = sorted(
        (cmd for cmd in visible if cmd not in parent_groups),
        key=lambda cmd: _command_sort_key(cmd, bot),
    )
    for shortcuts in shortcuts_by_target.values():
        shortcuts.sort()
    return groups, commands_to_render, shortcuts_by_target


def _category_subtitle(
    prefix: str, groups: list[commands.Group[None, ..., Any]]
) -> str:
    if not groups:
        return f"-# All commands also work with prefix `{prefix}`"
    lines: list[str] = []
    for group in groups:
        doc = (group.short_doc or "Group commands").rstrip(".")
        prefix_forms = [
            f"`{prefix}{group.name}`",
            *(f"`{prefix}{alias}`" for alias in group.aliases),
        ]
        lines.append(f"-# {doc} · Prefix: {' / '.join(prefix_forms)}")
    return "\n".join(lines)


def _home_category_line(info: CogHelpCategory) -> str:
    summary = info.description or "Browse commands in the menu below."
    return f"{info.emoji} **{info.label}** — {summary}"


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
                    description="Overview of all command categories.",
                    default=active_page == "home",
                ),
                *(
                    SelectOption(
                        label=info.label,
                        value=key.value,
                        emoji=info.emoji,
                        description=info.description,
                        default=active_page == key,
                    )
                    for key, info in categories.items()
                ),
            ],
        )
        select.callback = self._select_category

        if selected_category is None:
            heading = (
                "### 🌙 Milihu Help\n"
                "-# Pick a category from the menu below to browse commands."
            )
            body = "\n".join(
                _home_category_line(info) for info in categories.values()
            )
        else:
            groups, commands_in_category, shortcuts_by_target = (
                _collect_category_commands(bot, selected_category.items)
            )
            subtitle = _category_subtitle(prefix, groups)
            heading = f"### {selected_category.title}\n{subtitle}"
            body = "\n".join(
                self._command_line(
                    command,
                    shortcuts_by_target.get(command.qualified_name, ()),
                )
                for command in commands_in_category
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
                accent_colour=ACCENT_COLOUR,
            )
        )

    def _command_line(
        self,
        command: commands.Command[None, ..., Any],
        shortcuts: list[str] | tuple[str, ...],
    ) -> str:
        name = command.qualified_name
        has_slash = _has_slash_command(command, self.bot)
        pills: list[str] = [f"`/{name}`"] if has_slash else [f"`{self.prefix}{name}`"]
        if shortcuts:
            pills.extend(f"`{self.prefix}{shortcut}`" for shortcut in shortcuts)
        elif command.parent is None:
            if command.aliases:
                pills.extend(
                    f"`{self.prefix}{alias}`" for alias in command.aliases
                )
            elif has_slash:
                pills.append(f"`{self.prefix}{name}`")

        description = command.short_doc or "No description."
        return f"{' '.join(pills)} — {description}"

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

"""Cog for open discursive polls."""

from typing import override

from discord import (
    ButtonStyle,
    Client,
    Forbidden,
    HTTPException,
    Interaction,
    Member,
    TextStyle,
    User,
)
from discord.ext import commands
from discord.ui import (
    ActionRow,
    Button,
    Container,
    LayoutView,
    Modal,
    TextDisplay,
    TextInput,
)

from milihu.discord.cogs._base import BaseCog
from milihu.discord.types.bot import BotClient


class _PollAnswerModal(Modal, title="Answer Poll"):
    answer: TextInput[Modal] = TextInput(
        label="Your answer",
        style=TextStyle.paragraph,
        required=True,
        max_length=2000,
    )

    def __init__(
        self, author: User | Member, question: str, *, anonymous: bool
    ) -> None:
        super().__init__()
        self.author = author
        self.question = question
        self.anonymous = anonymous

    @override
    async def on_submit(self, interaction: Interaction[Client]) -> None:
        sender = (
            "-# Anonymous"
            if self.anonymous
            else f"-# From {interaction.user.mention} ({interaction.user})"
        )
        dm_view = LayoutView()
        dm_view.add_item(
            Container[LayoutView](
                TextDisplay[LayoutView]("### Poll Response"),
                TextDisplay[LayoutView](f"**Question:** {self.question}"),
                TextDisplay[LayoutView](self.answer.value),
                TextDisplay[LayoutView](sender),
            )
        )
        try:
            await self.author.send(view=dm_view)
        except (Forbidden, HTTPException):
            await interaction.response.send_message(
                "Could not deliver your answer to the poll author's DMs.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Your answer has been sent!",
            ephemeral=True,
        )


class _OpenPollView(LayoutView):
    def __init__(
        self, author: User | Member, question: str, *, anonymous: bool
    ) -> None:
        # shortcut: in-memory view state resets on bot restart, move to persistent 
        # custom_id/DB if needed across restarts
        super().__init__(timeout=None)
        self.author = author
        self.question = question
        self.anonymous = anonymous

        button: Button[_OpenPollView] = Button(
            label="Answer",
            style=ButtonStyle.primary,
        )
        button.callback = self._open_modal
        mode = "Anonymous" if anonymous else "Identified"
        self.add_item(
            Container(
                TextDisplay(f"### {question}"),
                TextDisplay(f"-# By {author.mention} • {mode}"),
                ActionRow(button),
            )
        )

    async def _open_modal(self, interaction: Interaction[BotClient]) -> None:
        await interaction.response.send_modal(
            _PollAnswerModal(self.author, self.question, anonymous=self.anonymous)
        )


class OpenpollCog(BaseCog):
    """Presents the open poll command."""

    @commands.hybrid_command(name="op", aliases=["openpoll"])
    async def openpoll(
        self,
        ctx: commands.Context[BotClient],
        anonymous: bool | None = True,
        *,
        question: str,
    ) -> None:
        """Open a discursive poll and receive answers via DM."""
        is_anonymous = True if anonymous is None else anonymous
        await ctx.reply(
            view=_OpenPollView(ctx.author, question, anonymous=is_anonymous)
        )

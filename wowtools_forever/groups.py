# Top-level command groups of WoWTools Forever.
#
# Discord allows at most 100 global slash commands per bot. To stay far below that
# limit, every slash command of this cog lives in one of two groups:
#   /wowtf    -> user commands (charinfo, gearcheck, token, cvar, ...)
#   /wowtfset -> settings (server, guild, scoreboard, me, bot)
# The mixins attach their subcommands with ``@wowtf.command(...)`` /
# ``@wowtfset.group(...)``; discord.py re-links them when the cog is instantiated.
from redbot.core import commands


async def send_group_help(ctx: commands.Context) -> None:
    """Show the group help when a prefix group is called without a subcommand.

    discord.py sets ``invoke_without_command=True`` on hybrid groups, which turns
    off Red's autohelp; former plain groups (wowtf-wowset, wowtf-raiderio) showed
    their help in that case, so the new groups do the same.
    """
    if ctx.interaction is None and ctx.invoked_subcommand is None and getattr(
        ctx.command, "invoke_without_command", False
    ):
        await ctx.send_help()


class WoWTFGroups:
    @commands.hybrid_group(name="wowtf")
    async def wowtf(self, ctx: commands.Context):
        """WoW Forever character, guild and game commands."""
        await send_group_help(ctx)

    @commands.hybrid_group(name="wowtfset")
    async def wowtfset(self, ctx: commands.Context):
        """WoWTools Forever settings (server, guild, your character, bot)."""
        await send_group_help(ctx)


wowtf = WoWTFGroups.wowtf
wowtfset = WoWTFGroups.wowtfset

import logging

from redbot.core import commands
from redbot.core.i18n import Translator, set_contextual_locales_from_guild

from .forever_api import get_lang, not_available_msg

log = logging.getLogger("red.karlo-cogs.wowtools_forever")
_ = Translator("WoWToolsForever", __file__)


class Scoreboard:
    """Guild scoreboards. The Classic/Retail scoreboard is a Mythic+ ranking based on
    Raider.IO; neither exists for WoW Forever, so every command answers with a
    friendly "not available" notice and no background updater is started."""

    async def _scoreboard_not_available(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await set_contextual_locales_from_guild(self.bot, ctx.guild)
        lang = await get_lang(self, ctx)
        await ctx.send(
            not_available_msg(lang, "Mythic+-Scoreboard", "Mythic+ scoreboard"), ephemeral=True
        )

    @commands.cooldown(rate=1, per=10, type=commands.BucketType.user)
    @commands.hybrid_group(name="wowtf-wowscoreboard", aliases=["wowtf-sb"])
    @commands.guild_only()
    async def wowscoreboard(self, ctx: commands.Context):
        """Show various scoreboards for your guild (not available in WoW Forever)."""
        pass

    @wowscoreboard.command(name="dungeon")
    @commands.guild_only()
    async def wowscoreboard_dungeon(self, ctx: commands.Context):
        """Get the Mythic+ scoreboard for this guild (not available in WoW Forever)."""
        await self._scoreboard_not_available(ctx)

    @commands.hybrid_group(name="wowtf-sbset")
    @commands.admin()
    @commands.guild_only()
    async def sbset(self, ctx):
        """Configure the guild scoreboard (not available in WoW Forever)."""
        pass

    @sbset.command(name="channel")
    @commands.admin()
    @commands.guild_only()
    async def sbset_channel(self, ctx: commands.Context):
        """Set the scoreboard channel (not available in WoW Forever)."""
        await self._scoreboard_not_available(ctx)

    @sbset.group(name="blacklist", aliases=["blocklist"])
    @commands.admin()
    @commands.guild_only()
    async def sbset_blacklist(self, ctx: commands.Context):
        """Manage the scoreboard blacklist (not available in WoW Forever)."""
        pass

    @sbset_blacklist.command(name="add")
    @commands.admin()
    @commands.guild_only()
    async def sbset_blacklist_add(self, ctx: commands.Context):
        """Add characters to the blacklist (not available in WoW Forever)."""
        await self._scoreboard_not_available(ctx)

    @sbset_blacklist.command(name="remove")
    @commands.admin()
    @commands.guild_only()
    async def sbset_blacklist_remove(self, ctx: commands.Context):
        """Remove characters from the blacklist (not available in WoW Forever)."""
        await self._scoreboard_not_available(ctx)

    @sbset_blacklist.command(name="list")
    @commands.admin()
    @commands.guild_only()
    async def sbset_blacklist_list(self, ctx: commands.Context):
        """List blacklisted characters (not available in WoW Forever)."""
        await self._scoreboard_not_available(ctx)

    @sbset_blacklist.command(name="clear")
    @commands.admin()
    @commands.guild_only()
    async def sbset_blacklist_clear(self, ctx: commands.Context):
        """Clear the blacklist (not available in WoW Forever)."""
        await self._scoreboard_not_available(ctx)

    @sbset.command(name="lock", hidden=True)
    @commands.admin()
    @commands.guild_only()
    async def sbset_lock(self, ctx: commands.Context):
        """Lock the scoreboard channel (not available in WoW Forever)."""
        await self._scoreboard_not_available(ctx)

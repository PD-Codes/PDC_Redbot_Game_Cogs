import logging
from typing import Optional

from discord import app_commands
from redbot.core import commands
from redbot.core.i18n import Translator, set_contextual_locales_from_guild

from .forever_api import get_lang, not_available_msg

log = logging.getLogger("red.karlo-cogs.wowtools_forever")
_ = Translator("WoWToolsForever", __file__)


class Raiderio:
    """Raider.IO commands. Raider.IO has no WoW Forever support, so all of them
    answer with a friendly "not available" notice instead of failing."""

    async def _raiderio_not_available(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await set_contextual_locales_from_guild(self.bot, ctx.guild)
        lang = await get_lang(self, ctx)
        await ctx.send(not_available_msg(lang, "Raider.IO"), ephemeral=True)

    @commands.cooldown(rate=1, per=5, type=commands.BucketType.user)
    @commands.group(name="wowtf-raiderio", aliases=["wowtf-rio"])
    async def raiderio(self, ctx: commands.Context):
        """Raider.IO commands (not available in WoW Forever)."""
        pass

    @raiderio.command(name="profile")
    @app_commands.describe(name="Character first name", surname="Character surname")
    async def raiderio_profile(
        self, ctx: commands.Context, name: Optional[str] = None, surname: Optional[str] = None
    ) -> None:
        """Display the Raider.IO profile of a character (not available in WoW Forever)."""
        await self._raiderio_not_available(ctx)

    @raiderio.command(name="guild")
    async def raiderio_guild(self, ctx: commands.Context, *, guild: Optional[str] = None) -> None:
        """Display the Raider.IO profile of a guild (not available in WoW Forever)."""
        await self._raiderio_not_available(ctx)

    @raiderio.command(name="affixes")
    async def raiderio_affixes(self, ctx: commands.Context, region: Optional[str] = None) -> None:
        """Display this week's Mythic+ affixes (not available in WoW Forever)."""
        if ctx.interaction:
            await set_contextual_locales_from_guild(self.bot, ctx.guild)
        lang = await get_lang(self, ctx)
        await ctx.send(
            not_available_msg(lang, "Mythic+ / Affixe", "Mythic+ / affixes"), ephemeral=True
        )

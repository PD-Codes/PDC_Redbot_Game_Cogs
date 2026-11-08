from typing import Optional

from redbot.core import commands
from redbot.core.i18n import Translator, set_contextual_locales_from_guild

from .forever_api import get_lang, not_available_msg

_ = Translator("WoWToolsForever", __file__)


class PvP:
    # WoW Forever (original game, level 60) has no arena/RBG/Solo Shuffle ratings and
    # no PvP ladder API, so this command only answers with a friendly notice.
    @commands.cooldown(rate=1, per=5, type=commands.BucketType.user)
    @commands.hybrid_command(name="wowtf-rating")
    async def rating(self, ctx, name: Optional[str] = None, surname: Optional[str] = None):
        """Check a character's PvP ratings (not available in WoW Forever)."""
        if ctx.interaction:
            # There is no contextual locale for interactions, so we need to set it manually
            # (This is probably a bug in Red, remove this when it's fixed)
            await set_contextual_locales_from_guild(self.bot, ctx.guild)
        lang = await get_lang(self, ctx)
        await ctx.send(
            not_available_msg(lang, "PvP-Ratings / Arena-Rangliste", "PvP ratings / arena ladder"),
            ephemeral=True,
        )

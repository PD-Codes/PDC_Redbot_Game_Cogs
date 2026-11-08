from datetime import datetime, timezone
from typing import Literal

import discord
from redbot.core import commands
from redbot.core.i18n import Translator, set_contextual_locales_from_guild

from .groups import wowtf
from .forever_api import ForeverAPIError, api_get, get_lang, not_available_msg
from .utils import format_to_gold

_ = Translator("WoWToolsForever", __file__)

VALID_REGIONS = ["eu", "us", "kr", "tw"]


async def _fetch_token_price(self, region: str, locale: str = "en_US") -> dict:
    """Queries the WoW Token price (dynamic-forever namespace).

    It is unknown whether WoW Forever has a WoW Token at all; a 403/404 raises
    ForeverAPIError with ``unavailable`` set.
    """
    return await api_get(self, region, "/data/wow/token/index", kind="dynamic", locale=locale)


class Token:
    @wowtf.command(name="token")
    async def wowtoken(self, ctx: commands.Context, region: Literal["eu", "us", "kr", "tw", "all"] = "eu"):
        """Check price of the WoW token in a region or `all` regions (if WoW Forever has one)."""
        if ctx.interaction and ctx.guild is not None:
            # Workaround for Red locale on interactions (not needed/possible in DMs)
            await set_contextual_locales_from_guild(self.bot, ctx.guild)

        lang = await get_lang(self, ctx)
        region = (region or "eu").lower()
        # The answer (price or "not available") is a normal public reply, so the
        # deferred response is public as well. After a defer, Discord keeps the
        # visibility of the defer for the first follow-up message.
        await ctx.defer(ephemeral=False)

        try:
            if region == "all":
                await self.priceall(ctx)
                return

            if region not in VALID_REGIONS:
                await ctx.send(
                    _("Invalid region. Valid regions are: `eu`, `us`, `kr`, `tw` or `all`."),
                    ephemeral=True,
                )
                return

            try:
                data = await _fetch_token_price(self, region=region, locale="en_US")
            except ForeverAPIError as e:
                if e.unavailable:
                    await ctx.send(not_available_msg(lang, "WoW-Token", "WoW Token"), ephemeral=True)
                    return
                raise
            price_copper = int(data.get("price", 0) or 0)
            if price_copper <= 0:
                await ctx.send(not_available_msg(lang, "WoW-Token", "WoW Token"), ephemeral=True)
                return

            gold_emotes = await self.config.emotes()
            message = _("Current price of the {region} WoW Token is: **{gold}**").format(
                region=region.upper(), gold=format_to_gold(price_copper, gold_emotes)
            )

            if ctx.guild is None or ctx.channel.permissions_for(ctx.guild.me).embed_links:
                embed = discord.Embed(description=message, colour=await ctx.embed_colour())
                ts = data.get("last_updated_timestamp")
                if ts:
                    dt = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
                    embed.set_footer(text=f"Last update: {dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
                await ctx.send(embed=embed)
            else:
                await ctx.send(message)

        except Exception as e:
            ephemeral = getattr(ctx, "interaction", None) is not None
            await ctx.send(_("Command failed successfully. {e}").format(e=e), ephemeral=ephemeral)

    async def priceall(self, ctx: commands.Context):
        """Check price of the WoW token in all supported regions"""
        lang = await get_lang(self, ctx)
        embed = discord.Embed(title=_("WoW Token prices"), colour=await ctx.embed_colour())
        for region in VALID_REGIONS:
            try:
                data = await _fetch_token_price(self, region=region, locale="en_US")
                price_copper = int(data.get("price", 0) or 0)
                gold_emotes = await self.config.emotes()
                embed.add_field(name=region.upper(), value=format_to_gold(price_copper, gold_emotes))
            except ForeverAPIError as e:
                value = not_available_msg(lang, "WoW-Token", "WoW Token") if e.unavailable else f"Error: {e}"
                embed.add_field(name=region.upper(), value=value)
            except Exception as e:
                embed.add_field(name=region.upper(), value=f"Error: {e}")

        if ctx.guild is None or ctx.channel.permissions_for(ctx.guild.me).embed_links:
            await ctx.send(embed=embed)
        else:
            msg = _("Current prices of the WoW Token in all regions:\n")
            for field in embed.fields:
                msg += f"{field.name}: {field.value}\n"
            await ctx.send(msg)

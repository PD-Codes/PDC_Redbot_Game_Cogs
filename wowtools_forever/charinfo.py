# WoWToolsForever/charinfo.py
from typing import Literal, Optional

import discord
from discord import app_commands
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n, set_contextual_locales_from_guild

from .autocomplete import (
    _LANG_CODES as AC_LANG_CODES,
)
from .groups import wowtf
from .forever_api import (
    display_name,
    explain_character_error,
    fetch_character,
    resolve_character_args,
)

_ = Translator("WoWToolsForever", __file__)

def _resolve_locale(lang_or_locale: str) -> str:
    if not lang_or_locale:
        return "en_US"
    key = lang_or_locale.lower()
    return AC_LANG_CODES.get(key, lang_or_locale)

def _pct(v: Optional[float]) -> str:
    try:
        return f"{float(v):.2f}%"
    except Exception:
        return "?"

def _fmt_rating_block(node: Optional[dict]) -> str:
    if not node:
        return "?"
    val = node.get("value")
    rn = node.get("rating_normalized")
    if val is None and rn is None:
        return "?"
    if val is None:
        return f"(RN {rn})"
    if rn is None:
        return _pct(val)
    return f"{_pct(val)} (RN {rn})"

async def _fetch_statistics(
    self,
    *,
    region: Literal["eu", "us", "kr", "tw"],
    name: str,
    surname: str,
    locale: str = "en_US",
) -> dict:
    return await fetch_character(self, region, name, surname, "statistics", locale=locale)

@cog_i18n(_)
class CharInfo(commands.Cog):
    """Show core character stats (HP, mana, primary stats, crit/haste/mastery, etc.)."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot

    @wowtf.command(
        name="charinfo",
        description="Show a character's core stats (HP, mana, primary stats, crit/haste/mastery).",
        extras={"i18n_desc": {
            "de-DE": "Zeigt die Kernwerte eines Charakters (HP, Mana, Primärwerte, Krit/Tempo/Meisterschaft).",
            "en-US": "Show a character's core stats (HP, mana, primary stats, crit/haste/mastery).",
        }},
    )
    @app_commands.describe(
        region="Region (eu/us/kr/tw); empty = your saved character region",
        name="Character first name; empty = your saved character",
        surname="Character surname; empty = your saved character",
        locale="Locale (e.g. de or de_DE, en or en_US)",
    )
    async def charinfo(
        self,
        ctx: commands.Context,
        region: Optional[Literal["eu", "us", "kr", "tw"]] = None,
        name: Optional[str] = None,
        surname: Optional[str] = None,
        locale: str = "en",
        private: bool = True,
    ):
        """Show general World of Warcraft character information."""
        game = "forever"
        if ctx.interaction:
            await set_contextual_locales_from_guild(self.bot, ctx.guild)
        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"
        region, name, surname, err = await resolve_character_args(self, ctx, region, name, surname)
        if err:
            return await ctx.send(err, ephemeral=True)
        locale = _resolve_locale(locale)

        try:
            await ctx.defer(ephemeral=private)
        except Exception:
            pass

        try:
            js = await _fetch_statistics(self, region=region, name=name, surname=surname, locale=locale)
        except Exception as e:
            msg = await explain_character_error(
                self, lang, e, region=region, name=name, surname=surname,
                feature_de="Charakterwerte", feature_en="Character stats",
                prefix_de="Fehler beim Abrufen der Charakterwerte",
                prefix_en="Failed to fetch character stats",
            )
            return await ctx.send(msg, ephemeral=bool(ctx.interaction))

        # Rohwerte
        health = js.get("health")
        power = js.get("power")
        power_type = (js.get("power_type") or {}).get("name")

        strength = (js.get("strength") or {}).get("effective")
        agility  = (js.get("agility") or {}).get("effective")
        intellect = (js.get("intellect") or {}).get("effective")
        stamina  = (js.get("stamina") or {}).get("effective")

        armor_eff = (js.get("armor") or {}).get("effective")
        spell_power = js.get("spell_power")

        melee_crit = _fmt_rating_block(js.get("melee_crit"))
        melee_haste = _fmt_rating_block(js.get("melee_haste"))
        spell_crit = _fmt_rating_block(js.get("spell_crit"))
        spell_haste = _fmt_rating_block(js.get("spell_haste"))
        ranged_crit = _fmt_rating_block(js.get("ranged_crit"))
        ranged_haste = _fmt_rating_block(js.get("ranged_haste"))
        mastery = _fmt_rating_block(js.get("mastery"))

        mana_regen = js.get("mana_regen")
        mana_regen_combat = js.get("mana_regen_combat")

        lines = []
        lines.append(f"**Health:** {health:,}" if isinstance(health, int) else f"**Health:** {health}")
        if power_type:
            lines.append(f"**{power_type}:** {power:,}" if isinstance(power, int) else f"**{power_type}:** {power}")

        lines.append("")
        lines.append(f"**Strength:** {strength}")
        lines.append(f"**Agility:** {agility}")
        lines.append(f"**Intellect:** {intellect}")
        lines.append(f"**Stamina:** {stamina}")

        lines.append("")
        lines.append(f"**Armor:** {armor_eff}")
        lines.append(f"**Spell Power:** {spell_power}")

        lines.append("")
        lines.append(f"**Melee Crit:** {melee_crit}")
        lines.append(f"**Melee Haste:** {melee_haste}")
        lines.append(f"**Ranged Crit:** {ranged_crit}")
        lines.append(f"**Ranged Haste:** {ranged_haste}")
        lines.append(f"**Spell Crit:** {spell_crit}")
        lines.append(f"**Spell Haste:** {spell_haste}")
        lines.append(f"**Mastery:** {mastery}")

        lines.append("")
        lines.append(f"**Mana Regen (ooc):** {mana_regen}")
        lines.append(f"**Mana Regen (combat):** {mana_regen_combat}")

        embed = discord.Embed(
            title=f"{display_name(name, surname)} ({region.upper()}) [{game.capitalize()}]",
            description="\n".join([x for x in lines if x is not None]),
            color=await ctx.embed_color(),
        )
        await ctx.send(embed=embed, ephemeral=private)

    # ---------- Autocomplete ----------
    @charinfo.autocomplete("locale")
    async def ac_locale(self, interaction: discord.Interaction, current: str):
        cur = (current or "").lower()
        display = {"de":"Deutsch","en":"English","fr":"Français","es":"Español","it":"Italiano","pt":"Português","ru":"Русский"}
        pairs = []
        for short, full in AC_LANG_CODES.items():
            label = display.get(short, short)
            pairs.append((f"{label} ({full})", full))
            pairs.append((f"{label} ({short})", short))
        return [app_commands.Choice(name=l, value=v) for l, v in pairs if cur in l.lower() or cur in v.lower()][:25]

async def setup(bot: Red):
    await bot.add_cog(CharInfo(bot))

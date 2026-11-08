# WoWToolsForever/talentcheck.py
from typing import List, Literal, Optional

import discord
from discord import app_commands
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n, set_contextual_locales_from_guild

# zentrale Tabellen aus autocomplete.py
from .autocomplete import (
    _LANG_CODES as AC_LANG_CODES,
)
from .groups import wowtf
from .forever_api import (
    display_name,
    explain_character_error,
    fetch_character,
    resolve_character_args,
    wowhead_url,
)
from .pdc_dashboard import tr_lang

_ = Translator("WoWToolsForever", __file__)

def _resolve_locale(lang_or_locale: str) -> str:
    if not lang_or_locale:
        return "en_US"
    key = lang_or_locale.lower()
    return AC_LANG_CODES.get(key, lang_or_locale)

# Embed descriptions are capped at 4096 characters; stay a bit below.
_DESC_LIMIT = 3900


def _truncate_lines(lines: List[str], limit: int = _DESC_LIMIT) -> str:
    """Join lines and cut at a line boundary so the text stays below ``limit``."""
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    out: List[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > limit - 2:
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out) + "\n…"


def _wowhead_spell(spell_id: int) -> str:
    return wowhead_url("spell", spell_id)

async def _fetch_specializations(
    self,
    *,
    region: Literal["eu", "us", "kr", "tw"],
    name: str,
    surname: str,
    locale: str = "en_US",
) -> dict:
    # Not verified for WoW Forever; a 403/404 is reported as "not available".
    return await fetch_character(self, region, name, surname, "specializations", locale=locale)

@cog_i18n(_)
class TalentCheck(commands.Cog):
    """Show a character's talents (WoW Forever, if the API provides them)."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot

    @wowtf.command(name="talentcheck")
    @app_commands.describe(
        region="Region (eu/us/kr/tw); empty = your saved character region",
        name="Character first name; empty = your saved character",
        surname="Character surname; empty = your saved character",
        locale="Locale (e.g. de or de_DE, en or en_US)",
    )
    async def talentcheck(
        self,
        ctx: commands.Context,
        region: Optional[Literal["eu", "us", "kr", "tw"]] = None,
        name: Optional[str] = None,
        surname: Optional[str] = None,
        locale: str = "en",
        private: bool = True,
    ):
        """Show a World of Warcraft character's talents."""
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
            data = await _fetch_specializations(
                self, region=region, name=name, surname=surname, locale=locale
            )
        except Exception as e:
            msg = await explain_character_error(
                self, lang, e, region=region, name=name, surname=surname,
                feature_de="Talente", feature_en="Talents",
                prefix_de="Fehler beim Abrufen der Talente", prefix_en="Failed to fetch talents",
            )
            return await ctx.send(msg, ephemeral=bool(ctx.interaction))

        active_spec = (data.get("active_specialization") or {}).get("name")
        specs = data.get("specializations") or []
        spec_lines: List[str] = []

        for spec in specs:
            spec_name = spec.get("specialization_name") or (spec.get("specialization") or {}).get("name") or "?"
            header = f"**{spec_name}**" + ((tr_lang(lang, "  *(aktiv)*", "  *(active)*")) if active_spec and spec_name == active_spec else "")
            spec_lines.append(header)

            # Talente
            talents = spec.get("talents") or []
            if talents:
                for t in talents:
                    tt = t.get("spell_tooltip") or {}
                    spell = (tt.get("spell") or {})
                    spell_name = spell.get("name") or (t.get("talent") or {}).get("name") or "?"
                    spell_id = spell.get("id")
                    if spell_id:
                        spec_lines.append(f"`└──` **Talent:** [{spell_name}]({_wowhead_spell(spell_id)})")
                    else:
                        spec_lines.append(f"`└──` **Talent:** {spell_name}")

        # Aktive Glyphen: aus specialization_groups is_active == true
        glyph_lines: List[str] = []
        for grp in data.get("specialization_groups", []) or []:
            if grp.get("is_active"):
                glyphs = grp.get("glyphs") or []
                if glyphs:
                    glyph_lines.append(tr_lang(lang, "**Aktive Glyphen**", "**Active Glyphs**"))
                    for g in glyphs:
                        glyph_name = g.get("name") or "?"
                        glyph_lines.append(f"`└──` {glyph_name}")
                break  # nur die aktive Gruppe

        if not spec_lines and not glyph_lines:
            return await ctx.send(
                tr_lang(lang, "Keine Talent-/Glyphen-Daten gefunden.", "No talent/glyph data found."),
                ephemeral=bool(ctx.interaction),
            )

        embed = discord.Embed(
            title=f"{display_name(name, surname)} ({region.upper()}) [{game.capitalize()}]",
            description=_truncate_lines(spec_lines + ([""] if spec_lines and glyph_lines else []) + glyph_lines),
            color=await ctx.embed_color(),
        )
        ephemeral = private if ctx.interaction else False
        await ctx.send(embed=embed, ephemeral=ephemeral)

    # ---------- Autocomplete ----------
    @talentcheck.autocomplete("locale")
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
    await bot.add_cog(TalentCheck(bot))

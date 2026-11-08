# WoWToolsForever/charstats.py
from typing import List, Literal, Optional

import discord
from discord import app_commands
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n, set_contextual_locales_from_guild

from .autocomplete import (
    _LANG_CODES as AC_LANG_CODES,
)
from .forever_api import (
    display_name,
    explain_character_error,
    fetch_character,
    resolve_character_args,
)
from .pdc_dashboard import tr_lang

_ = Translator("WoWToolsForever", __file__)

def _resolve_locale(lang_or_locale: str) -> str:
    if not lang_or_locale:
        return "en_US"
    key = lang_or_locale.lower()
    return AC_LANG_CODES.get(key, lang_or_locale)

async def _fetch_achv_statistics(
    self,
    *,
    region: Literal["eu", "us", "kr", "tw"],
    name: str,
    surname: str,
    locale: str = "en_US",
) -> dict:
    return await fetch_character(self, region, name, surname, "achievements/statistics", locale=locale)

def _find_stat(stats: List[dict], name_contains: str) -> Optional[dict]:
    name_contains = name_contains.lower()
    for st in stats or []:
        if (st.get("name") or "").lower().find(name_contains) >= 0:
            return st
    return None

def _collect_all_stats(js: dict) -> List[dict]:
    out: List[dict] = []
    # Top-level statistics
    for st in js.get("statistics") or []:
        out.append(st)
    # categories (recursive)
    def walk_cat(cat: dict):
        for st in cat.get("statistics") or []:
            out.append(st)
        for sub in cat.get("sub_categories") or []:
            walk_cat(sub)
    for cat in js.get("categories") or []:
        walk_cat(cat)
    return out

@cog_i18n(_)
class CharStats(commands.Cog):
    """Show highlights from the achievement statistics (kills, deaths, quests, instances, records)."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="wowtf-charstats",
        description="Show achievement statistics highlights (kills, deaths, quests, instances, records).",
        extras={"i18n_desc": {
            "de-DE": "Zeigt Highlights der Erfolgs-Statistiken (Kills, Tode, Quests, Instanzen, Rekorde).",
            "en-US": "Show achievement statistics highlights (kills, deaths, quests, instances, records).",
        }},
    )
    @app_commands.describe(
        region="Region (eu/us/kr/tw); empty = your saved character region",
        name="Character first name; empty = your saved character",
        surname="Character surname; empty = your saved character",
        #locale="Locale (e.g. de or de_DE, en or en_US)",
    )
    async def charstats(
        self,
        ctx: commands.Context,
        region: Optional[Literal["eu", "us", "kr", "tw"]] = None,
        name: Optional[str] = None,
        surname: Optional[str] = None,
        #locale: str = "en",
        private: bool = True,
    ):
        """Show detailed stats for a World of Warcraft character."""
        game = "forever"
        if ctx.interaction:
            await set_contextual_locales_from_guild(self.bot, ctx.guild)
        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"
        region, name, surname, err = await resolve_character_args(self, ctx, region, name, surname)
        if err:
            return await ctx.send(err, ephemeral=True)
        #locale = _resolve_locale(locale)

        try:
            await ctx.defer(ephemeral=private)
        except Exception:
            pass

        try:
            js = await _fetch_achv_statistics(self, region=region, name=name, surname=surname)
        except Exception as e:
            msg = await explain_character_error(
                self, lang, e, region=region, name=name, surname=surname,
                feature_de="Erfolgs-Statistiken", feature_en="Achievement statistics",
                prefix_de="Fehler beim Abrufen der Achievements-Statistiken",
                prefix_en="Failed to fetch achievement statistics",
            )
            return await ctx.send(msg, ephemeral=bool(ctx.interaction))

        all_stats = _collect_all_stats(js)

        # einige sinnvolle Highlights herausziehen (falls vorhanden)
        picks = {
            "Total kills": _find_stat(all_stats, "Total kills"),
            "Total deaths": _find_stat(all_stats, "Total deaths"),
            "Quests completed": _find_stat(all_stats, "Quests completed"),
            "Dungeons entered (5p)": _find_stat(all_stats, "Total 5-player dungeons entered"),
            "Raids entered (10p)": _find_stat(all_stats, "Total 10-player raids entered"),
            "Raids entered (25p)": _find_stat(all_stats, "Total 25-player raids entered"),
            "Total damage done": _find_stat(all_stats, "Total damage done"),
            "Total damage received": _find_stat(all_stats, "Total damage received"),
            "Total healing done": _find_stat(all_stats, "Total healing done"),
            "Total healing received": _find_stat(all_stats, "Total healing received"),
            "Largest hit dealt": _find_stat(all_stats, "Largest hit dealt"),
            "Largest heal cast": _find_stat(all_stats, "Largest heal cast"),
            "_sep1":"---------------------------",
            "Bandages used": _find_stat(all_stats, "Bandages used"),
            "Health potions consumed": _find_stat(all_stats, "Health potions consumed"),
            "Mana potions consumed": _find_stat(all_stats, "Mana potions consumed"),
            "Elixirs consumed": _find_stat(all_stats, "Elixirs consumed"),
            "Flasks consumed": _find_stat(all_stats, "Flasks consumed"),
            "Beverages consumed": _find_stat(all_stats, "Beverages consumed"),
            "Food eaten": _find_stat(all_stats, "Food eaten"),
            "Healthstones used": _find_stat(all_stats, "Healthstones used"),
            "_sep2":"---------------------------",
            "Factions Exalted": _find_stat(all_stats, "Most factions at Exalted"),
            "Mounts owned": _find_stat(all_stats, "Mounts owned"),
            "Greed rolls made on loot": _find_stat(all_stats, "Greed rolls made on loot"),
            "Need rolls made on loot": _find_stat(all_stats, "Need rolls made on loot"),
            "Deaths from falling": _find_stat(all_stats, "Deaths from falling"),
            "_sep3":"---------------------------",
            "Creatures killed": _find_stat(all_stats, "Creatures killed"),
            "Total Honorable Kills": _find_stat(all_stats, "Total Honorable Kills"),
            "_sep4":"---------------------------",
            "Flight paths taken": _find_stat(all_stats, "Flight paths taken"),
            "Summons accepted": _find_stat(all_stats, "Summons accepted"),
            "Mage Portals taken": _find_stat(all_stats, "Mage Portals taken"),
            "Number of times hearthed": _find_stat(all_stats, "Number of times hearthed"),
        }

        lines: List[str] = []
        for label, st in picks.items():
            # Separator-Zeilen erkennen
            if label.startswith("_sep"):
                lines.append(str(st))
                continue

            if isinstance(st, dict) and "quantity" in st:
                q = st["quantity"]
                if isinstance(q, (int, float)):
                    if isinstance(q, float) and q.is_integer():
                        q = int(q)
                    lines.append(f"**{label}:** {q:,}")
                else:
                    lines.append(f"**{label}:** {q}")


        if not lines:
            lines = [tr_lang(lang, "Keine passenden Statistiken gefunden.", "No matching statistics found.")]

        embed = discord.Embed(
            title=f"{display_name(name, surname)} ({region.upper()}) [{game.capitalize()}]",
            description="\n".join(lines),
            color=await ctx.embed_color(),
        )
        ephemeral = private if ctx.interaction else False
        await ctx.send(embed=embed, ephemeral=ephemeral)

    # ---------- Autocomplete ----------
    #@charstats.autocomplete("locale")
    #async def ac_locale(self, interaction: discord.Interaction, current: str):
    #    cur = (current or "").lower()
    #    display = {"de":"Deutsch","en":"English","fr":"Français","es":"Español","it":"Italiano","pt":"Português","ru":"Русский"}
    #    pairs = []
    #    for short, full in AC_LANG_CODES.items():
    #        label = display.get(short, short)
    #        pairs.append((f"{label} ({full})", full))
    #        pairs.append((f"{label} ({short})", short))
    #    return [app_commands.Choice(name=l, value=v) for l, v in pairs if cur in l.lower() or cur in v.lower()][:25]

async def setup(bot: Red):
    await bot.add_cog(CharStats(bot))

# WoWToolsForever/comparechars.py
import asyncio
from typing import List, Dict, Literal, Optional

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
    fetch_item_levels,
)

_ = Translator("WoWToolsForever", __file__)

# ----------------- Helpers -----------------
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

# ----------------- Fetchers -----------------
async def _fetch_equipment(self, *, region: str, name: str, surname: str, locale: str) -> dict:
    return await fetch_character(self, region, name, surname, "equipment", locale=locale)

async def _fetch_item_levels(self, *, region: str, locale: str, item_ids: List[int], concurrency: int = 5) -> Dict[int, Optional[int]]:
    return await fetch_item_levels(self, region, item_ids, locale=locale, concurrency=concurrency)

async def _fetch_statistics(self, *, region: str, name: str, surname: str, locale: str) -> dict:
    return await fetch_character(self, region, name, surname, "statistics", locale=locale)

async def _fetch_achv_statistics(self, *, region: str, name: str, surname: str, locale: str) -> dict:
    return await fetch_character(self, region, name, surname, "achievements/statistics", locale=locale)

def _avg_ilvl(ilvls: List[Optional[int]]) -> Optional[float]:
    vals = [x for x in ilvls if isinstance(x, int)]
    if not vals:
        return None
    return sum(vals) / len(vals)

def _build_gear_compare_lines(eq1: dict, eq2: dict, ilvls1: Dict[int, Optional[int]], ilvls2: Dict[int, Optional[int]]) -> List[str]:
    def add(equipped: List[dict], store: Dict[str, Optional[int]], mapping: Dict[int, Optional[int]]):
        for it in equipped or []:
            slot = it.get("slot", {}).get("name")
            iid = (it.get("item") or {}).get("id")
            lvl = mapping.get(iid)
            if slot:
                store[slot] = lvl

    s1: Dict[str, Optional[int]] = {}
    s2: Dict[str, Optional[int]] = {}
    add(eq1.get("equipped_items") or [], s1, ilvls1)
    add(eq2.get("equipped_items") or [], s2, ilvls2)

    all_slots = sorted(set(list(s1.keys()) + list(s2.keys())), key=lambda x: x.lower())
    lines: List[str] = []

    # average
    avg1 = _avg_ilvl(list(s1.values()))
    avg2 = _avg_ilvl(list(s2.values()))
    if avg1 is not None or avg2 is not None:
        lines.append(f"**Ø Itemlevel:** {f'{avg1:.1f}' if avg1 is not None else '?'}  vs  {f'{avg2:.1f}' if avg2 is not None else '?'}")
        lines.append("")

    for slot in all_slots:
        v1 = s1.get(slot)
        v2 = s2.get(slot)
        l1 = f"{v1}" if v1 is not None else "?"
        l2 = f"{v2}" if v2 is not None else "?"
        lines.append(f"**{slot}:** {l1}  ⟷  {l2}")

    return lines

def _build_info_compare_lines(js1: dict, js2: dict) -> List[str]:
    def grab(js, key, sub=None, eff=False):
        node = js.get(key) or {}
        if sub:
            return node.get(sub)
        if eff:
            return node.get("effective")
        return js.get(key)

    lines: List[str] = []
    # Basics
    health1, health2 = js1.get("health"), js2.get("health")
    ptype1, ptype2 = (js1.get("power_type") or {}).get("name"), (js2.get("power_type") or {}).get("name")
    power1, power2 = js1.get("power"), js2.get("power")

    lines.append(f"**Health:** {health1:,}  ⟷  {health2:,}" if isinstance(health1, int) and isinstance(health2, int) else f"**Health:** {health1}  ⟷  {health2}")
    if ptype1 == ptype2 and ptype1:
        # same power type
        if isinstance(power1, int) and isinstance(power2, int):
            lines.append(f"**{ptype1}:** {power1:,}  ⟷  {power2:,}")
        else:
            lines.append(f"**{ptype1}:** {power1}  ⟷  {power2}")
    else:
        lines.append(f"**Power:** {power1} ({ptype1})  ⟷  {power2} ({ptype2})")

    lines.append("")
    # primary stats
    for k in ("strength", "agility", "intellect", "stamina"):
        v1 = (js1.get(k) or {}).get("effective")
        v2 = (js2.get(k) or {}).get("effective")
        lines.append(f"**{k.title()}:** {v1}  ⟷  {v2}")

    lines.append("")
    armor1 = (js1.get("armor") or {}).get("effective")
    armor2 = (js2.get("armor") or {}).get("effective")
    sp1, sp2 = js1.get("spell_power"), js2.get("spell_power")
    lines.append(f"**Armor:** {armor1}  ⟷  {armor2}")
    lines.append(f"**Spell Power:** {sp1}  ⟷  {sp2}")

    lines.append("")
    # Ratings
    pairs = [
        ("Melee Crit", _fmt_rating_block(js1.get("melee_crit")), _fmt_rating_block(js2.get("melee_crit"))),
        ("Melee Haste", _fmt_rating_block(js1.get("melee_haste")), _fmt_rating_block(js2.get("melee_haste"))),
        ("Ranged Crit", _fmt_rating_block(js1.get("ranged_crit")), _fmt_rating_block(js2.get("ranged_crit"))),
        ("Ranged Haste", _fmt_rating_block(js1.get("ranged_haste")), _fmt_rating_block(js2.get("ranged_haste"))),
        ("Spell Crit", _fmt_rating_block(js1.get("spell_crit")), _fmt_rating_block(js2.get("spell_crit"))),
        ("Spell Haste", _fmt_rating_block(js1.get("spell_haste")), _fmt_rating_block(js2.get("spell_haste"))),
        ("Mastery", _fmt_rating_block(js1.get("mastery")), _fmt_rating_block(js2.get("mastery"))),
    ]
    for label, a, b in pairs:
        lines.append(f"**{label}:** {a}  ⟷  {b}")

    return lines

def _collect_all_stats_nodes(js: dict) -> list[dict]:
    out = []
    for st in js.get("statistics") or []:
        out.append(st)
    def walk(cat: dict):
        for st in cat.get("statistics") or []:
            out.append(st)
        for sub in cat.get("sub_categories") or []:
            walk(sub)
    for cat in js.get("categories") or []:
        walk(cat)
    return out

def _find_stat_id_by_en_name(all_en: list[dict], needle: str) -> Optional[int]:
    n = needle.lower()
    for st in all_en:
        if n in (st.get("name") or "").lower():
            return st.get("id")
    return None

def _build_charstats_compare_lines_en(js1_en: dict, js2_en: dict) -> list[str]:
    all1 = _collect_all_stats_nodes(js1_en)
    all2 = _collect_all_stats_nodes(js2_en)
    by_id_1 = {st["id"]: st for st in all1 if "id" in st}
    by_id_2 = {st["id"]: st for st in all2 if "id" in st}

    # Definitions (English pattern -> label in the output)
    defs = [
        ("Total kills", "Total kills"),
        ("Total deaths", "Total deaths"),
        ("Quests completed", "Quests completed"),
        ("Total 5-player dungeons entered", "Dungeons entered (5p)"),
        ("Total 10-player raids entered", "Raids entered (10p)"),
        ("Total 25-player raids entered", "Raids entered (25p)"),
        ("Total damage done", "Total damage done"),
        ("Total damage received", "Total damage received"),
        ("Total healing done", "Total healing done"),
        ("Total healing received", "Total healing received"),
        ("Largest hit dealt", "Largest hit dealt"),
        ("Largest heal cast", "Largest heal cast"),
        ("SEP", None),
        ("Bandages used", "Bandages used"),
        ("Health potions consumed", "Health potions consumed"),
        ("Mana potions consumed", "Mana potions consumed"),
        ("Elixirs consumed", "Elixirs consumed"),
        ("Flasks consumed", "Flasks consumed"),
        ("Beverages consumed", "Beverages consumed"),
        ("Food eaten", "Food eaten"),
        ("Healthstones used", "Healthstones used"),
        ("SEP", None),
        ("Most factions at Exalted", "Factions Exalted"),
        ("Mounts owned", "Mounts owned"),
        ("Greed rolls made on loot", "Greed rolls made on loot"),
        ("Need rolls made on loot", "Need rolls made on loot"),
        ("Deaths from falling", "Deaths from falling"),
        ("SEP", None),
        ("Creatures killed", "Creatures killed"),
        ("Total Honorable Kills", "Total Honorable Kills"),
        ("SEP", None),
        ("Flight paths taken", "Flight paths taken"),
        ("Summons accepted", "Summons accepted"),
        ("Mage Portals taken", "Mage Portals taken"),
        ("Number of times hearthed", "Number of times hearthed"),
    ]

    lines: list[str] = []
    for pattern, label in defs:
        if pattern == "SEP":
            lines.append("—" * 27)
            continue
        # look up the same ID in both – we match based on char1 (robust enough)
        stat_id = _find_stat_id_by_en_name(all1, pattern)
        if not stat_id:
            continue
        n1 = by_id_1.get(stat_id)
        n2 = by_id_2.get(stat_id)
        if not n1 or not n2:
            continue
        q1, q2 = n1.get("quantity"), n2.get("quantity")
        # format number nicely
        def fmt(x):
            if isinstance(x, (int, float)):
                if isinstance(x, float) and x.is_integer():
                    x = int(x)
                return f"{x:,}"
            return str(x)
        lines.append(f"**{label}:** {fmt(q1)}  ⟷  {fmt(q2)}")
    return lines


# ----------------- Cog -----------------
@cog_i18n(_)
class CompareChars(commands.Cog):
    """Compare two characters: gear (item level per slot) OR character stats."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot

    @wowtf.command(
        name="comparechars",
        description="Compare two characters by gear (item level per slot), info or statistics.",
        extras={"i18n_desc": {
            "de-DE": "Vergleicht zwei Charaktere nach Ausrüstung (Itemlevel pro Slot), Werten oder Statistiken.",
            "en-US": "Compare two characters by gear (item level per slot), info or statistics.",
        }},
    )
    @app_commands.describe(
        region="Region (eu/us/kr/tw)",
        name_char1="First name of character 1",
        surname_char1="Surname of character 1",
        name_char2="First name of character 2",
        surname_char2="Surname of character 2",
        type="Comparison type: 'gear' or 'info'",
        locale="Locale (e.g. de or de_DE, en or en_US)",
        private="Show the response only to you (ephemeral)",
    )
    @app_commands.choices(
        type=[
            app_commands.Choice(name="Gear", value="gear"),
            app_commands.Choice(name="Info", value="info"),
            app_commands.Choice(name="CharStats (en_US)", value="charstats"),
        ],
    )
    async def comparechars(
        self,
        ctx: commands.Context,
        region: Literal["eu", "us", "kr", "tw"],
        name_char1: str,
        surname_char1: str,
        name_char2: str,
        surname_char2: str,
        type: str,
        locale: str = "en",
        private: bool = True,
    ):
        """Compare two World of Warcraft: Forever characters."""
        game = "forever"
        if ctx.interaction:
            await set_contextual_locales_from_guild(self.bot, ctx.guild)

        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"
        region = (region or "").lower()
        locale = _resolve_locale(locale)
        type = (type or "").lower()
        c1 = {"name": name_char1, "surname": surname_char1}
        c2 = {"name": name_char2, "surname": surname_char2}

        # Slash: defer correctly right away (set ephemeral beforehand!)
        if ctx.interaction:
            try:
                await ctx.defer(ephemeral=private)
            except Exception:
                pass

        # Which character is currently being fetched (for a precise 404 message).
        current = c1
        try:
            if type == "gear":
                feature = ("Ausrüstung", "Equipment")
                current = c1
                eq1 = await _fetch_equipment(self, region=region, locale=locale, **c1)
                current = c2
                eq2 = await _fetch_equipment(self, region=region, locale=locale, **c2)
                ids1 = [it.get("item", {}).get("id") for it in (eq1.get("equipped_items") or []) if it.get("item")]
                ids2 = [it.get("item", {}).get("id") for it in (eq2.get("equipped_items") or []) if it.get("item")]
                il1, il2 = await asyncio.gather(
                    _fetch_item_levels(self, region=region, locale=locale, item_ids=ids1),
                    _fetch_item_levels(self, region=region, locale=locale, item_ids=ids2),
                )
                lines = _build_gear_compare_lines(eq1, eq2, il1, il2)
                title_mid = "Gear"
            elif type == "info":
                feature = ("Charakterwerte", "Character stats")
                current = c1
                js1 = await _fetch_statistics(self, region=region, locale=locale, **c1)
                current = c2
                js2 = await _fetch_statistics(self, region=region, locale=locale, **c2)
                lines = _build_info_compare_lines(js1, js2)
                title_mid = "Info"
            else:  # "charstats"
                feature = ("Erfolgs-Statistiken", "Achievement statistics")
                # Always use en_US for achievement statistics comparisons, regardless of requested locale.
                current = c1
                js1_en = await _fetch_achv_statistics(self, region=region, locale="en_US", **c1)
                current = c2
                js2_en = await _fetch_achv_statistics(self, region=region, locale="en_US", **c2)
                lines = _build_charstats_compare_lines_en(js1_en, js2_en)
                title_mid = "CharStats (en_US)"

        except Exception as e:
            msg = await explain_character_error(
                self, lang, e, region=region, name=current["name"], surname=current["surname"],
                feature_de=feature[0], feature_en=feature[1],
                prefix_de="Fehler beim Abruf", prefix_en="Failed to fetch data",
            )
            return await ctx.send(msg, ephemeral=(private if ctx.interaction else False))

        # Build embed
        title = f"{display_name(name_char1, surname_char1)}  ⟷  {display_name(name_char2, surname_char2)}"
        embed = discord.Embed(
            title=f"{title} – {region.upper()} [{game.capitalize()}] ({title_mid})",
            description="\n".join(lines)[:3900],
            color=await ctx.embed_color(),
        )
        await ctx.send(embed=embed, ephemeral=(private if ctx.interaction else False))

    # ---------- Autocomplete ----------
    @comparechars.autocomplete("locale")
    async def ac_locale(self, interaction: discord.Interaction, current: str):
        cur = (current or "").lower()
        display = {"de":"Deutsch","en":"English","fr":"Français","es":"Español","it":"Italiano","pt":"Português","ru":"Русский"}
        pairs = []
        for short, full in AC_LANG_CODES.items():
            label = display.get(short, short)
            pairs.append((f"{label} ({full})", full))
            pairs.append((f"{label} ({short})", short))
        return [app_commands.Choice(name=l, value=v) for l, v in pairs if not cur or cur in l.lower() or cur in v.lower()][:25]

async def setup(bot: Red):
    await bot.add_cog(CompareChars(bot))

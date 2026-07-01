# WoWTools/charstats.py
import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Literal, Dict, Optional

import discord
from discord import app_commands
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n, set_contextual_locales_from_guild

from .autocomplete import (
    REGIONS,
    REALMS as AC_REALMS,
    fetch_realm_names,
    _LANG_CODES as AC_LANG_CODES,
    _API_HOST,
    _AUTH_HOST,
)
from .pdc_dashboard import tr_lang

_ = Translator("WoWTools", __file__)

def _resolve_locale(lang_or_locale: str) -> str:
    if not lang_or_locale:
        return "en_US"
    key = lang_or_locale.lower()
    return AC_LANG_CODES.get(key, lang_or_locale)

# OAuth Cache
def _ensure_oauth_state(self):
    if not hasattr(self, "_tok_lock"):
        self._tok_lock = asyncio.Lock()
    if not hasattr(self, "_tok"):
        self._tok: Dict[str, str] = {}
    if not hasattr(self, "_exp"):
        self._exp: Dict[str, datetime] = {}

async def _get_access_token(self, region: str) -> str:
    _ensure_oauth_state(self)
    async with self._tok_lock:
        now = datetime.now(timezone.utc)
        if self._tok.get(region) and self._exp.get(region) and now < self._exp[region]:
            return self._tok[region]
        api_tokens = await self.bot.get_shared_api_tokens("blizzard")
        cid, secret = api_tokens.get("client_id"), api_tokens.get("client_secret")
        if not cid or not secret:
            raise RuntimeError("Blizzard API nicht eingerichtet. `[p]set api blizzard client_id,<id> client_secret,<secret>`")
        auth_host = _AUTH_HOST.get(region, "eu.battle.net")
        url = f"https://{auth_host}/oauth/token"
        async with aiohttp.ClientSession() as s:
            async with s.post(url, data={"grant_type": "client_credentials"}, auth=aiohttp.BasicAuth(cid, secret)) as resp:
                js = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"Auth {resp.status}: {js}")
        token = js["access_token"]
        expires_in = int(js.get("expires_in", 3600))
        self._tok[region] = token
        self._exp[region] = now + timedelta(seconds=max(30, expires_in - 30))
        return token

async def _fetch_achv_statistics(
    self,
    *,
    region: Literal["eu", "us", "kr", "tw"],
    realm: str,
    character: str,
    game: Literal["classic", "retail"] = "retail",
) -> dict:
    locale = "en_US",
    host = _API_HOST.get(region, "eu.api.blizzard.com")
    token = await _get_access_token(self, region)
    namespace = f"profile-{region}"
    url = f"https://{host}/profile/wow/character/{realm}/{character}/achievements/statistics"
    params = {"namespace": namespace, "locale": locale}
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params, headers=headers) as resp:
            js = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"{resp.status}: {js}")
            return js

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
        name="wowt-charstats",
        description="Show achievement statistics highlights (kills, deaths, quests, instances, records).",
        extras={"i18n_desc": {
            "de-DE": "Zeigt Highlights der Erfolgs-Statistiken (Kills, Tode, Quests, Instanzen, Rekorde).",
            "en-US": "Show achievement statistics highlights (kills, deaths, quests, instances, records).",
        }},
    )
    @app_commands.describe(
        region="Region (eu/us/kr/tw)",
        realm="Realm (use a hyphen instead of spaces)",
        character="Character name",
        #locale="Locale (e.g. de or de_DE, en or en_US)",
    )
    async def charstats(
        self,
        ctx: commands.Context,
        region: Literal["eu", "us", "kr", "tw"],
        realm: str,
        character: str,
        #locale: str = "en",
        private: bool = True,
    ):
        """Show detailed stats for a World of Warcraft character."""
        game = "retail"
        if ctx.interaction:
            await set_contextual_locales_from_guild(self.bot, ctx.guild)
        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"
        region = region.lower()
        #locale = _resolve_locale(locale)
        realm_slug = realm.lower().replace(" ", "-")
        char_slug = character.lower()

        try:
            await ctx.defer(ephemeral=private)
        except Exception:
            pass

        try:
            js = await _fetch_achv_statistics(self, region=region, realm=realm_slug, character=char_slug, game=game)#, locale=locale)
        except Exception as e:
            return await ctx.send(
                tr_lang(lang, f"Fehler beim Abrufen der Achievements-Statistiken: {e}", f"Failed to fetch achievement statistics: {e}"),
                ephemeral=bool(ctx.interaction),
            )

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
            "Quests completed": _find_stat(all_stats, "Quests completed"),
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
            title=f"{character.title()} – {realm.title()} ({region.upper()}) [{game.capitalize()}]",
            description="\n".join(lines),
            color=await ctx.embed_color(),
        )
        ephemeral = private if ctx.interaction else False
        await ctx.send(embed=embed, ephemeral=ephemeral)

    # ---------- Autocomplete ----------
    @charstats.autocomplete("realm")
    async def ac_realm(self, interaction: discord.Interaction, current: str):
        sel_region = getattr(interaction.namespace, "region", "") or "eu"
        cur = (current or "").lower()
        names = await fetch_realm_names(lambda r: _get_access_token(self, r), sel_region)
        out = [n for n in names if not cur or cur in n.lower()]
        return [app_commands.Choice(name=r, value=r) for r in out[:25]]

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

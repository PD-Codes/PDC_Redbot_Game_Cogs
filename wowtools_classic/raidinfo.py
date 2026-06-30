# WoWTools/raidinfo.py
import aiohttp
import asyncio
import re
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Literal, Optional, Tuple

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
    _EXPENSIONS,
)
from .pdc_dashboard import tr_lang

_ = Translator("WoWTools", __file__)

def _resolve_locale(lang_or_locale: str) -> str:
    if not lang_or_locale:
        return "en_US"
    key = lang_or_locale.lower()
    return AC_LANG_CODES.get(key, lang_or_locale)

# -------- OAuth Cache --------
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

# -------- Fetch --------
async def _fetch_achv_statistics(
    self,
    *,
    region: str,
    realm: str,
    character: str,
    game: str = "classic",
    locale: str = "en_US",
) -> dict:
    host = _API_HOST.get(region, "eu.api.blizzard.com")
    token = await _get_access_token(self, region)
    namespace = f"profile-classic-{region}"
    url = f"https://{host}/profile/wow/character/{realm}/{character}/achievements/statistics"
    params = {"namespace": namespace, "locale": locale}
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params, headers=headers) as resp:
            js = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"{resp.status}: {js}")
            return js

# -------- Helpers --------
_VERBS = ("kills", "victories", "rescues", "defeats", "defeated")

_RE_LINE_GENERIC = re.compile(r"^(?P<left>.+?)\s*\((?P<detail>.+?)\)\s*$")

# -------- Generic collectors for Dungeons & Raids (with expansion tagging) --------
def _collect_all_raid_stats_with_expansion(js: dict) -> List[dict]:
    """
    Walks categories → 'Dungeons & Raids' and returns all statistics from all expansions.
    Each returned dict contains at least: {'name': str, 'quantity': int, 'expansion': str}.
    """
    out: List[dict] = []

    def coerce_int(q):
        if isinstance(q, (int, float)):
            return int(q)
        return None

    # Find the 'Dungeons & Raids' root
    for cat in js.get("categories") or []:
        if (cat.get("name") or "").lower() != "dungeons & raids":
            continue

        # At the first level below, nodes are usually expansions (Classic, TBC, Wrath, Cataclysm, Mists of Pandaria, ...)
        def walk(node: dict, current_expansion: Optional[str]):
            # Collect stats at this node (tag with current_expansion if present)
            for st in node.get("statistics") or []:
                name = st.get("name") or ""
                q = coerce_int(st.get("quantity"))
                if name and q is not None:
                    out.append({"name": name, "quantity": q, "expansion": current_expansion or ""})

            # Recurse into subcategories; expansion sticks unless a child looks like a new expansion root
            for sc in node.get("sub_categories") or []:
                sc_name = sc.get("name") or ""
                # Heuristic: if we're directly under the D&R root, treat this name as the expansion name.
                next_exp = current_expansion
                if (node is cat):  # immediate children of D&R are expansions
                    next_exp = sc_name
                walk(sc, next_exp)

        # Start walk with no expansion at the root, it will be set at first sublevel
        walk(cat, None)
        break

    return out

def _clean_tokens_for_raid(detail: str) -> Tuple[str, str]:
    """
    Returns (raid_name, diff) where diff ∈ {'hc','nhc'}.
    Detects 'Heroic'/'Normal' tokens in detail and removes '10/25 player' noise.
    """
    diff = "nhc"
    d = detail.strip()

    if re.search(r"\bheroic\b", d, re.IGNORECASE):
        diff = "hc"

    d = re.sub(r"\b(10|25)\s*-\s*player\b", "", d, flags=re.IGNORECASE)
    d = re.sub(r"\b(10|25)\s*player\b", "", d, flags=re.IGNORECASE)
    d = re.sub(r"\b(10|25)-player\b", "", d, flags=re.IGNORECASE)
    d = re.sub(r"\b(normal|heroic)\b", "", d, flags=re.IGNORECASE)
    raid = re.sub(r"\s{2,}", " ", d).strip()
    return raid, diff

def _parse_raid_stat_name(name: str) -> Optional[Tuple[str, str, str]]:
    """
    Returns (boss, raid, diff) or None.
    diff ∈ {"nhc","hc"}.
    Only considers lines that contain a recognized verb and have a "(...)" trailer.
    """
    if not name:
        return None

    m = _RE_LINE_GENERIC.match(name)
    if not m:
        return None

    left = m.group("left").strip()
    detail = m.group("detail").strip()

    verb_match = None
    for v in _VERBS:
        vm = re.search(rf"\b{re.escape(v)}\b", left, flags=re.IGNORECASE)
        if vm:
            verb_match = (v, vm.start(), vm.end())
            break
    if not verb_match:
        return None

    _, s, _ = verb_match

    is_heroic_left = "heroic" in left.lower()
    boss = left[:s].strip()
    boss = re.sub(r"\bheroic\b", "", boss, flags=re.IGNORECASE).strip()

    raid, diff_from_detail = _clean_tokens_for_raid(detail)
    diff = "hc" if (is_heroic_left or diff_from_detail == "hc") else "nhc"

    if not boss or not raid:
        return None

    return boss, raid, diff


def _group_by_expansion_and_raid(stats: List[dict], only_expansion: Optional[str]) -> Dict[str, Dict[str, Dict[str, Dict[str, int]]]]:
    """
    Build:
    {
      "<Expansion>": {
        "<Raid>": {
          "<Boss>": {"nhc": int, "hc": int}
        }
      }
    }
    Optionally filter by expansion name (case-insensitive substring match).
    """
    out: Dict[str, Dict[str, Dict[str, Dict[str, int]]]] = {}
    exp_filter = (only_expansion or "").strip().lower() if only_expansion else ""

    for st in stats:
        name = st.get("name") or ""
        q = st.get("quantity")
        exp = (st.get("expansion") or "").strip()
        if not isinstance(q, int):
            continue

        parsed = _parse_raid_stat_name(name)
        if not parsed:
            continue
        boss, raid, diff = parsed

        # expansion filter (substring on the human-readable expansion label)
        if exp_filter and exp_filter not in exp.lower():
            continue

        out.setdefault(exp, {})
        out[exp].setdefault(raid, {})
        out[exp][raid].setdefault(boss, {"nhc": 0, "hc": 0})
        out[exp][raid][boss][diff] += q

    # sort: expansions by a sensible order (fallback: alpha), raids alpha, bosses alpha
    # Try to respect a canonical expansion order if available
    def exp_sort_key(e_name: str) -> Tuple[int, str]:
        order = {
            "Classic": 1,
            "The Burning Crusade": 2,
            "Wrath of the Lich King": 3,
            "Cataclysm": 4,
            "Mists of Pandaria": 5,
        }
        return (order.get(e_name, 999), e_name.lower())

    sorted_out: Dict[str, Dict[str, Dict[str, Dict[str, int]]]] = {}
    for exp in sorted(out.keys(), key=exp_sort_key):
        raids = out[exp]
        sorted_raids: Dict[str, Dict[str, Dict[str, int]]] = {}
        for raid in sorted(raids.keys(), key=lambda s: s.lower()):
            bosses = raids[raid]
            sorted_bosses = dict(sorted(bosses.items(), key=lambda kv: kv[0].lower()))
            sorted_raids[raid] = sorted_bosses
        sorted_out[exp] = sorted_raids

    return sorted_out

def _format_embed_text(grouped: Dict[str, Dict[str, Dict[str, Dict[str, int]]]], lang: str = "en-US") -> str:
    """
    Expansion
    RAID

    Boss
    Normal Kills => X
    Heroic Kills => Y
    """
    lines: List[str] = []
    for expansion, raids in grouped.items():
        if expansion:
            lines.append(f"**{expansion}**")
        else:
            lines.append(tr_lang(lang, "**(Unbekannte Expansion)**", "**(Unknown expansion)**"))

        if not raids:
            lines.append(tr_lang(lang, "_Keine Raids_", "_No raids_"))
            lines.append("")  # blank line between expansions
            continue

        for raid, bosses in raids.items():
            lines.append(f"{raid}\n")
            if not bosses:
                lines.append(tr_lang(lang, "_Keine Bossdaten_\n", "_No boss data_\n"))
                continue

            for boss, counts in bosses.items():
                nhc = counts.get("nhc", 0)
                hc = counts.get("hc", 0)
                lines.append(f"{boss}")
                lines.append(f"Normal Kills => {nhc}")
                lines.append(f"Heroic Kills => {hc}\n")  # blank line after each boss

        lines.append("")  # blank line between expansions

    text = "\n".join(lines).strip()
    return text[:3800]


@cog_i18n(_)
class RaidInfo(commands.Cog):
    """Lists raid bosses with kill counters by difficulty (nhc/hc), grouped by Expansion → Raid → Boss."""


    def __init__(self, bot: Red) -> None:
        self.bot = bot

    @commands.hybrid_command(name="wowtc-raidinfo")
    @app_commands.describe(
        region="Region (eu/us/kr/tw)",
        realm="Realm (use a hyphen instead of spaces)",
        character="Character name",
        locale="Locale (e.g. de or de_DE, en or en_US)",
        extension="Optional filter (raid name, e.g. 'Mogu'shan Vaults'). Empty = all MoP raids.",
    )
    async def raidinfo(
        self,
        ctx: commands.Context,
        region: Literal["eu", "us", "kr", "tw"],
        realm: str,
        character: str,
        locale: str = "en_US",
        extension: Optional[str] = None,
        private: bool = True,
    ):
        game = "classic"
        if ctx.interaction:
            await set_contextual_locales_from_guild(self.bot, ctx.guild)

        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"
        region = (region or "").lower()
        locale = _resolve_locale(locale)
        realm_slug = realm.lower().replace(" ", "-")
        char_slug = character.lower()

        try:
            await ctx.defer(ephemeral=private)
        except Exception:
            pass

        try:
            js = await _fetch_achv_statistics(
                self, region=region, realm=realm_slug, character=char_slug, game=game, locale=locale
            )
        except Exception as e:
            return await ctx.send(
                tr_lang(lang, f"Fehler beim Abrufen der Achievements-Statistiken: {e}", f"Failed to fetch achievement statistics: {e}"),
                ephemeral=bool(ctx.interaction),
            )

        all_stats = _collect_all_raid_stats_with_expansion(js)
        if not all_stats:
            return await ctx.send(
                tr_lang(lang, "Keine Dungeon/Raid-Statistiken gefunden.", "No dungeon/raid statistics found."),
                ephemeral=bool(ctx.interaction),
            )

        grouped = _group_by_expansion_and_raid(all_stats, extension)
        if not grouped:
            flt = extension or "—"
            return await ctx.send(
                tr_lang(lang, f"Keine passenden Einträge für Expansion-Filter **{flt}**.", f"No matching entries for expansion filter **{flt}**."),
                ephemeral=bool(ctx.interaction),
            )

        text = _format_embed_text(grouped, lang)
        embed = discord.Embed(
            title=f"{character.title()} – {realm.title()} ({region.upper()}) [{game.capitalize()}] – Raids",
            description=text,
            color=await ctx.embed_color(),
        )
        ephemeral = private if ctx.interaction else False
        await ctx.send(embed=embed, ephemeral=ephemeral)


    # ---------- Autocomplete ----------
    @raidinfo.autocomplete("realm")
    async def ac_realm(self, interaction: discord.Interaction, current: str):
        sel_region = getattr(interaction.namespace, "region", "") or "eu"
        cur = (current or "").lower()
        names = await fetch_realm_names(lambda r: _get_access_token(self, r), sel_region)
        out = [n for n in names if not cur or cur in n.lower()]
        return [app_commands.Choice(name=r, value=r) for r in out[:25]]

    @raidinfo.autocomplete("locale")
    async def ac_locale(self, interaction: discord.Interaction, current: str):
        cur = (current or "").lower()
        display = {"de_DE":"Deutsch","en_US":"English"}
        pairs = []
        for short, full in AC_LANG_CODES.items():
            label = display.get(short, short)
            pairs.append((f"{label} ({full})", full))
            pairs.append((f"{label} ({short})", short))
        return [app_commands.Choice(name=l, value=v) for l, v in pairs if cur in l.lower() or cur in v.lower()][:25]

    @raidinfo.autocomplete("extension")
    async def ac_extension(self, interaction: discord.Interaction, current: str):
        # This is an expansion filter (substring). Offer common expansion labels.
        cur = (current or "").lower()

        # Try to use provided expansions from .autocomplete if present,
        # otherwise fall back to a static list.
        labels: List[str] = []
        try:
            # _EXPENSIONS might be dict-like or list-like; handle both.
            if hasattr(_EXPENSIONS, "items"):
                # Prefer human-readable values if dict maps codes -> labels
                vals = list(getattr(_EXPENSIONS, "values")())
                keys = list(getattr(_EXPENSIONS, "keys")())
                labels = [str(v) for v in vals] + [str(k) for k in keys]
            else:
                labels = [str(x) for x in list(_EXPENSIONS)]
        except Exception:
            labels = [
                "Classic",
                "The Burning Crusade",
                "Wrath of the Lich King",
                "Cataclysm",
                "Mists of Pandaria",
            ]

        # Make unique and stable
        seen = set()
        uniq = []
        for lbl in labels:
            L = lbl.strip()
            if not L or L.lower() in seen:
                continue
            seen.add(L.lower())
            uniq.append(L)

        opts = [e for e in uniq if not cur or cur in e.lower()]
        return [app_commands.Choice(name=o, value=o) for o in opts[:25]]


async def setup(bot: Red):
    await bot.add_cog(RaidInfo(bot))

# WoWTools/gearcheck.py
import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Literal, Optional, Dict

import discord
from discord import app_commands
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n, set_contextual_locales_from_guild
from .autocomplete import REALMS as AC_REALMS, REGIONS as AC_REGIONS, _LANG_CODES, _API_HOST, _AUTH_HOST, fetch_realm_names
from .pdc_dashboard import tr_lang

_ = Translator("WoWTools", __file__)

def _resolve_locale(lang_or_locale: str) -> str:
    if not lang_or_locale:
        return "en_US"
    key = lang_or_locale.lower()
    return _LANG_CODES.get(key, lang_or_locale)  # "de" -> "de_DE", passes full locales through

def _wowhead_url(item_id: int, game: Literal["classic", "retail"]) -> str:
    # MoP Classic has its own path
    return f"https://wowhead.com/item={item_id}"

def _quality_emoji(quality_type: str) -> str:
    q = (quality_type or "").upper()
    return {
        "LEGENDARY": "🟧",
        "EPIC": "🟪",
        "RARE": "🟦",
        "UNCOMMON": "🟩",
        "COMMON": "⬜",
    }.get(q, "🔳")

def _is_socket_enchant(ench: dict) -> bool:
    """
    Heuristic: gem sockets usually have source_item (the gem) and slot IDs 1..4.
    BONUS_SOCKETS (id: 6) is just an extra socket, not a gem itself.
    """
    if not ench:
        return False
    if ench.get("source_item"):
        slot = ench.get("enchantment_slot", {}) or {}
        slot_id = slot.get("id")
        return slot_id in {1, 2, 3, 4}
    return False

def _ensure_gear_oauth_state(self):
    if not hasattr(self, "_gear_lock"):
        self._gear_lock = asyncio.Lock()
    if not hasattr(self, "_gear_tok"):
        self._gear_tok = {}          # region -> token
    if not hasattr(self, "_gear_exp"):
        self._gear_exp = {}          # region -> expires_at (datetime)

async def _get_access_token_cached_gear(self, region: str) -> str:
    _ensure_gear_oauth_state(self)
    async with self._gear_lock:
        now = datetime.now(timezone.utc)
        tok = self._gear_tok.get(region)
        exp = self._gear_exp.get(region)
        if tok and exp and now < exp:
            return tok

        api_tokens = await self.bot.get_shared_api_tokens("blizzard")
        cid, secret = api_tokens.get("client_id"), api_tokens.get("client_secret")
        if not cid or not secret:
            raise RuntimeError(
                "Blizzard API nicht eingerichtet. Nutze: "
                "`[p]set api blizzard client_id,<id> client_secret,<secret>`"
            )

        auth_host = _AUTH_HOST.get(region, "eu.battle.net")
        url = f"https://{auth_host}/oauth/token"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, data={"grant_type": "client_credentials"},
                auth=aiohttp.BasicAuth(cid, secret),
            ) as resp:
                js = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"Auth {resp.status}: {js}")
        token = js["access_token"]
        expires_in = int(js.get("expires_in", 3600))
        # small buffer
        self._gear_tok[region] = token
        self._gear_exp[region] = now + timedelta(seconds=max(30, expires_in - 30))
        return token

async def _fetch_equipment_blizzard(self, *, region: str, realm: str, character: str,
                                    game: str = "retail", locale: str = "en_US") -> dict:
    host = _API_HOST.get(region, "eu.api.blizzard.com")
    token = await _get_access_token_cached_gear(self, region)
    realm_slug = realm.lower().replace(" ", "-")
    char_slug = character.lower()
    namespace = f"profile-{region}"
    url = f"https://{host}/profile/wow/character/{realm_slug}/{char_slug}/equipment"
    params = {"namespace": namespace, "locale": locale}
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=headers) as resp:
            js = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"{resp.status}: {js}")
            return js


@cog_i18n(_)
class GearCheck(commands.Cog):
    """Gear check via the Blizzard Profile API (Classic/Retail), incl. detailed iLvl fetch and 'Socket' label."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        # OAuth-Cache pro Region
        self._lock = asyncio.Lock()
        self._tok: Dict[str, str] = {}
        self._exp: Dict[str, datetime] = {}

    # ---------------- OAuth ----------------
    async def _get_access_token_cached(self, region: str) -> str:
        async with self._lock:
            now = datetime.now(timezone.utc)
            tok = self._tok.get(region)
            exp = self._exp.get(region)
            if tok and exp and now < exp:
                return tok

            api_tokens = await self.bot.get_shared_api_tokens("blizzard")
            cid, secret = api_tokens.get("client_id"), api_tokens.get("client_secret")
            if not cid or not secret:
                raise RuntimeError(
                    "Blizzard API nicht eingerichtet. Nutze: "
                    "`[p]set api blizzard client_id,<id> client_secret,<secret>`"
                )

            auth_host = _AUTH_HOST.get(region, "eu.battle.net")
            url = f"https://{auth_host}/oauth/token"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data={"grant_type": "client_credentials"},
                    auth=aiohttp.BasicAuth(cid, secret),
                ) as resp:
                    js = await resp.json()
                    if resp.status != 200:
                        raise RuntimeError(f"Auth {resp.status}: {js}")
            token = js["access_token"]
            expires_in = int(js.get("expires_in", 3600))
            # small buffer
            self._tok[region] = token
            self._exp[region] = now + timedelta(seconds=max(30, expires_in - 30))
            return token

    # --------------- Blizzard API: Character Equipment ---------------
    async def _fetch_equipment(
        self,
        *,
        region: Literal["eu", "us", "kr"],
        realm: str,
        character: str,
        game: Literal["classic", "retail"] = "retail",
        locale: str = "en_US",
        private: bool = True,
    ) -> dict:
        host = _API_HOST.get(region, "eu.api.blizzard.com")
        token = await _get_access_token_cached_gear(self, region)
        realm_slug = realm.lower().replace(" ", "-")
        char_slug = character.lower()

        namespace = f"profile-{region}"
        url = f"https://{host}/profile/wow/character/{realm_slug}/{char_slug}/equipment"
        params = {"namespace": namespace, "locale": locale}
        headers = {"Authorization": f"Bearer {token}"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                js = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"{resp.status}: {js}")
                return js

    # --------------- Blizzard API: Item-Level pro Item-ID ---------------
    async def _fetch_item_levels(
        self,
        *,
        region: str,
        game: str,
        locale: str,
        item_ids: List[int],
        concurrency: int = 5,
    ) -> Dict[int, Optional[int]]:
        """
        Fetches the item level per item ID from /data/wow/item/{id}.
        Returns Dict {item_id: level or None}.
        """
        host = _API_HOST.get(region, "eu.api.blizzard.com")
        token = await _get_access_token_cached_gear(self, region)
        namespace = f"static-{region}"

        sem = asyncio.Semaphore(concurrency)
        results: Dict[int, Optional[int]] = {}

        async def fetch_one(session: aiohttp.ClientSession, iid: int):
            url = f"https://{host}/data/wow/item/{iid}"
            params = {"namespace": namespace, "locale": locale}
            headers = {"Authorization": f"Bearer {token}"}
            async with sem:
                async with session.get(url, params=params, headers=headers) as resp:
                    js = await resp.json()
                    if resp.status == 200:
                        results[iid] = js.get("level")
                    else:
                        results[iid] = None

        uniq_ids = list({i for i in item_ids if i})
        if not uniq_ids:
            return {}

        async with aiohttp.ClientSession() as session:
            await asyncio.gather(*(fetch_one(session, iid) for iid in uniq_ids))

        return results

    # --------------- Command ---------------
    @commands.hybrid_command(
        name="wowt-gearcheck",
        description="Show a character's equipped gear with item level, sockets and enchants.",
        extras={"i18n_desc": {
            "de-DE": "Zeigt die angelegte Ausrüstung eines Charakters mit Itemlevel, Sockeln und Verzauberungen.",
            "en-US": "Show a character's equipped gear with item level, sockets and enchants.",
        }},
    )
    @app_commands.describe(
        region="Region (eu/us/kr/tw)",
        realm="Realm (use a hyphen instead of spaces)",
        character="Character name",
        locale="Locale (e.g. de or de_DE, en or en_US)",
    )
    async def gearcheck(self, ctx, region: Literal["eu", "us", "kr", "tw"], realm: str, character: str,
                    locale: str = "en", private: bool = True):
        """Show a character's currently equipped gear (incl. iLvl fetch & socket/enchant labels)."""
        game = "retail"
        if ctx.interaction:
            await set_contextual_locales_from_guild(self.bot, ctx.guild)

        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"
        region = region.lower()

        locale = _resolve_locale(locale)

        try:
            await ctx.defer(ephemeral=private)
        except Exception:
            pass

        # 1) Load equipment
        try:
            data = await _fetch_equipment_blizzard(
                self, region=region.lower(), realm=realm.lower(), character=character.lower(),
                game=game.lower(), locale=locale
            )
        except Exception as e:
            return await ctx.send(
                tr_lang(lang, f"Fehler beim Abrufen der Ausrüstung: {e}", f"Failed to fetch gear: {e}"),
                ephemeral=bool(ctx.interaction),
            )

        equipped = data.get("equipped_items") or []
        if not equipped:
            return await ctx.send(_("No gear found."))

        # 2) Reload item level per item
        item_ids = [it.get("item", {}).get("id") for it in equipped if it.get("item")]
        ilvls_by_id = await self._fetch_item_levels(
            region=region, game=game, locale=locale, item_ids=item_ids
        )

        # 3) Build output (mind the 2000-char limit)
        lines: List[str] = []
        hidden_count = 0

        for it in equipped:
            try:
                slot_name = it["slot"]["name"]
                quality_type = it.get("quality", {}).get("type", "COMMON")
                emoji = _quality_emoji(quality_type)
                name = it.get("name", "Unknown")
                item_id = it.get("item", {}).get("id")
                ilvl = ilvls_by_id.get(item_id)
                ilvl_str = f"ilvl {ilvl}" if ilvl is not None else "ilvl ?"

                link = _wowhead_url(item_id, game) if item_id else None
                head = (
                    f"**{slot_name}**: {emoji} [{name}]({link}) ({ilvl_str})"
                    if link
                    else f"**{slot_name}**: {emoji} {name} ({ilvl_str})"
                )
                lines.append(head)

                # Enchants / Sockets
                for ench in it.get("enchantments", []) or []:
                    d = ench.get("display_string")
                    if not d:
                        continue
                    if _is_socket_enchant(ench):
                        lines.append(f"`└──` **Socket:** {d}")
                    else:
                        lines.append(f"`└──` **Enchant:** {d}")

            except Exception:
                # Defensive: a single broken item shouldn't kill everything
                continue

            # Soft limit so we stay under 2000 characters
            if sum(len(x) + 1 for x in lines) > 1800:
                hidden_count = max(0, len(equipped) - len(lines))
                break

        if hidden_count > 0:
            lines.append(tr_lang(lang, f"... und {hidden_count} weitere Einträge.", f"... and {hidden_count} more entries."))

        embed = discord.Embed(
            title=f"{character.title()} – {realm.title()} ({region.upper()}) [{game.capitalize()}]",
            description="\n".join(lines),
            color=await ctx.embed_color(),
        )

        ephemeral = private if ctx.interaction else False
        await ctx.send(embed=embed, ephemeral=private)

    # --------- Autocomplete ---------
    @gearcheck.autocomplete("realm")
    async def ac_realm(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        sel_region = getattr(interaction.namespace, "region", "") or "eu"
        cur = (current or "").lower()
        names = await fetch_realm_names(lambda r: _get_access_token_cached_gear(self, r), sel_region)
        out = [n for n in names if not cur or cur in n.lower()]
        return [app_commands.Choice(name=r, value=r) for r in out[:25]]

    @gearcheck.autocomplete("locale")
    async def ac_locale(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        cur = (current or "").lower()

        # Build suggestions: ("Deutsch (de_DE)", "de_DE") + ("Deutsch (de)", "de")
        display_map = {
            "de": "Deutsch", "en": "English", "fr": "Français", "es": "Español",
            "it": "Italiano", "pt": "Português", "ru": "Русский",
        }

        pairs: List[tuple[str, str]] = []
        for short, full in AC_LANG_CODES.items():
            label_base = display_map.get(short, short)
            # full locale
            pairs.append((f"{label_base} ({full})", full))
            # short code
            pairs.append((f"{label_base} ({short})", short))

        # filter
        out = [
            app_commands.Choice(name=label, value=val)
            for (label, val) in pairs
            if cur in label.lower() or cur in val.lower()
        ][:25]
        return out



async def setup(bot: Red):
    await bot.add_cog(GearCheck(bot))

# Most of the source of this file for the actual API mechanics can be found at:
# https://github.com/Kowlin/GraphQL-WoWLogs/blob/master/wowlogs/core.py
#
# WoW: Forever variant (forever.warcraftlogs.com). Runs side by side with the
# WarcraftLogsClassic and WarcraftLogsRetail cogs in the same bot.

import asyncio
import io
import logging
import math
import time
from datetime import datetime, timezone
from typing import Dict, List, Literal, Mapping, Optional, Tuple

import discord
from beautifultable import ALIGN_LEFT, BeautifulTable
from discord import app_commands
from PIL import Image, ImageDraw, ImageFont
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.data_manager import bundled_data_path
from redbot.core.i18n import Translator, cog_i18n, set_contextual_locales_from_guild
from redbot.core.utils.chat_formatting import box, humanize_list

from .autocomplete import REGIONS
from .http import WoWLogsClient, generate_bearer
from .lookup import (
    CharacterResolver,
    LookupResult,
    normalize_name,
    parse_character_input,
    split_name,
)
from .pdc_dashboard import (
    dashboard_widget, dashboard_panel,
    WidgetData, PanelSchema, Field, SubmitResult,
    register_dashboard, unregister_dashboard,
    L, tr, tr_lang,
)
from .wclEnums import SLOT_ORDER_MOP

# NOTE: enchantid.py (MoP enchants/gems) and encounterid.py (MoP zones) are
# Classic leftovers and intentionally NOT imported here. Forever zones,
# encounters and difficulties are fetched live from the API.

_ = Translator("WarcraftLogsForever", __file__)
log = logging.getLogger("red.karlo-cogs.warcraftlogs-forever")

WCL_BASE = "https://forever.warcraftlogs.com"
WCL_URL = WCL_BASE + "/reports/{}"
WOWHEAD_ITEM_URL = "https://www.wowhead.com/forever/item={}"

# How long (seconds) to cache the live zone list before re-fetching.
ZONES_CACHE_TTL = 3600
# Max number of zones probed when no zone was requested (rate-limit friendly).
ZONE_PROBE_LIMIT = 10

# Gear slot order: WCL lists gear by inventory slot; the Classic enum is reused
# (Vanilla-based Forever uses the same 19 equipment slots).
SLOT_ORDER = SLOT_ORDER_MOP


@cog_i18n(_)
class WarcraftLogsForever(commands.Cog):
    """Retrieve World of Warcraft: Forever character information from WarcraftLogs."""

    def __init__(self, bot):
        self.bot: Red = bot
        self.config = Config.get_conf(self, identifier=87446677010550786, force_registration=True)
        self.http: Optional[WoWLogsClient] = None
        self.path = bundled_data_path(self)

        # The Forever bearer is stored in this cog's OWN config (NOT the shared
        # "warcraftlogs" tokens) so it never clobbers the classic/retail bearers.
        self.config.register_global(bearer_timestamp=0, bearer=None)

        # Live zone cache (Forever zones/encounters are not known in advance).
        self._zones_cache: Optional[list] = None
        self._zones_cache_time: float = 0.0

        # In-memory character resolver (undocumented, best-effort name lookup).
        self.resolver = CharacterResolver()

        # Last seen shared client credentials (to ignore bearer-only token updates
        # written by the classic cog).
        self._client_creds: Tuple[str, str] = ("", "")
        # Serializes bearer renewal (concurrent 401s / expiry / token updates).
        self._token_lock = asyncio.Lock()

        default_user = {
            "charname": None,
            "surname": None,
            "region": None,
        }
        default_guild = {
            "notification_channel": None,
            "language": "en-US",
        }

        self.config.register_user(**default_user)
        self.config.register_guild(**default_guild)

    # ------------------------------------------------------------------ #
    # Auth / client
    # ------------------------------------------------------------------ #
    async def _read_client_creds(
        self, api_tokens: Optional[Mapping[str, str]] = None
    ) -> Tuple[str, str]:
        if api_tokens is None:
            api_tokens = await self.bot.get_shared_api_tokens("warcraftlogs")
        return (api_tokens.get("client_id", "") or "", api_tokens.get("client_secret", "") or "")

    async def _create_client(self) -> None:
        """Create the API client. Never raises, so the cog always loads;
        commands report missing/invalid credentials instead."""
        self._client_creds = await self._read_client_creds()
        self.http = WoWLogsClient(bearer=await self._get_bearer(), refresher=self._refresh_bearer)
        # A 401 (or non-JSON) answer regenerates the bearer once inside _post.
        await self.http.check_bearer()

    async def _refresh_bearer(self, stale: Optional[str] = None, force: bool = False) -> Optional[str]:
        """Generate a new bearer and swap the client session (serialized).

        ``stale`` is the bearer that failed or expired; if another task already
        replaced it while this one waited for the lock, the current bearer is
        reused instead of requesting yet another one. ``force`` always requests
        a new bearer (e.g. after the shared client credentials changed)."""
        async with self._token_lock:
            if (
                not force
                and self.http is not None
                and self.http._bearer
                and self.http._bearer != stale
            ):
                return self.http._bearer
            bearer = await generate_bearer(self.bot, self.config)
            if bearer is None:
                return None
            if self.http is None:
                self.http = WoWLogsClient(bearer=bearer, refresher=self._refresh_bearer)
            else:
                await self.http.recreate_session(bearer)
            return bearer

    async def _ensure_client(self) -> bool:
        """Make sure a session with a non-expired bearer exists (bearers expire)."""
        if self.http is None:
            await self._create_client()
            return bool(self.http and self.http._bearer)
        timestamp_now = int(datetime.now(timezone.utc).timestamp())
        if not self.http._bearer or timestamp_now > await self.config.bearer_timestamp():
            await self._refresh_bearer(self.http._bearer)
        return bool(self.http._bearer)

    async def _get_bearer(self) -> Optional[str]:
        # Forever bearer lives in this cog's OWN config (not shared tokens).
        bearer = await self.config.bearer()

        bearer_timestamp = await self.config.bearer_timestamp()
        timestamp_now = int(datetime.now(timezone.utc).timestamp())

        if timestamp_now > bearer_timestamp:
            log.info("Bearer token has expired. Generating one")
            bearer = await generate_bearer(self.bot, self.config)
        elif not bearer:
            log.info("Bearer token doesn't exist. Generating one")
            bearer = await generate_bearer(self.bot, self.config)

        if bearer is None:
            return
        return bearer

    async def _get_zones(self, force: bool = False) -> list:
        """Return the live WCL zone list, cached on the cog instance.

        Forever zone IDs are unknown and new raids will appear over time, so
        they are fetched from the API instead of being hardcoded. The result is
        cached for ``ZONES_CACHE_TTL`` seconds."""
        now = time.monotonic()
        if (
            not force
            and self._zones_cache is not None
            and (now - self._zones_cache_time) < ZONES_CACHE_TTL
        ):
            return self._zones_cache

        if self.http is None:
            return self._zones_cache or []

        zones = await self.http.get_zones()
        if zones is None:
            # Keep any stale cache rather than returning nothing.
            return self._zones_cache or []

        self._zones_cache = zones
        self._zones_cache_time = now
        return zones

    # ------------------------------------------------------------------ #
    # Messages (bilingual)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _msg_auth(lang: str, prefix: str) -> str:
        return tr_lang(
            lang,
            "Warcraft Logs Forever hat die API-Zugangsdaten abgelehnt (401) oder es konnte kein "
            f"Token erzeugt werden. Prüfe `{prefix}wclset-forever apikey`.\n"
            "Hinweis: Es ist nicht bestätigt, dass WCL-API-Clients auch für "
            "forever.warcraftlogs.com gelten.",
            "Warcraft Logs Forever rejected the API credentials (401) or no token could be "
            f"created. Check `{prefix}wclset-forever apikey`.\n"
            "Note: it is not confirmed that WCL API clients are valid for "
            "forever.warcraftlogs.com.",
        )

    @staticmethod
    def _msg_not_found(lang: str, who: str) -> str:
        return tr_lang(
            lang,
            f"Charakter **{who}** wurde auf forever.warcraftlogs.com nicht gefunden.\n"
            "Tipp: Nutze statt des Namens den WCL-Link (z. B. "
            "`https://forever.warcraftlogs.com/character/id/12345`) oder die numerische "
            "Charakter-ID. Der Charakter muss auf forever.warcraftlogs.com existieren "
            "(hochgeladenes Log oder per Battle.net geclaimt).",
            f"Character **{who}** was not found on forever.warcraftlogs.com.\n"
            "Tip: use the WCL link (e.g. "
            "`https://forever.warcraftlogs.com/character/id/12345`) or the numeric "
            "character ID instead of the name. The character must exist on "
            "forever.warcraftlogs.com (uploaded log or claimed via Battle.net).",
        )

    # ------------------------------------------------------------------ #
    # Character resolution
    # ------------------------------------------------------------------ #
    async def _resolve_character(
        self,
        ctx: commands.Context,
        lang: str,
        name: Optional[str],
        surname: Optional[str],
        region: Optional[str],
    ) -> Optional[Tuple[dict, dict, str]]:
        """Resolve user input to a WCL character.

        Accepts a WCL character link, a numeric WCL character id, or
        name (+ optional surname) + region. Falls back to the user's saved
        settings. Returns ``(char_ref, character, title)`` or sends an error
        message and returns None."""
        userdata = await self.config.user(ctx.author).all()

        if not name:
            name = userdata["charname"]
            if not surname:
                surname = userdata["surname"]
        elif not surname and userdata["charname"] and name.lower() == str(userdata["charname"]).lower():
            surname = userdata["surname"]

        # Prefix-command convenience: "[p]wcl-forever gear Name EU"
        if surname and not region and surname.upper() in REGIONS:
            region, surname = surname, None

        if not name:
            await ctx.send(
                tr_lang(
                    lang,
                    "Bitte gib einen Charakternamen (Name + Nachname), einen WCL-Link oder eine "
                    f"Charakter-ID an oder speichere deinen Charakter mit `{ctx.clean_prefix}wclset-forever`.",
                    "Please specify a character name (name + surname), a WCL link or a character ID, "
                    f"or save your character with `{ctx.clean_prefix}wclset-forever`.",
                ),
                ephemeral=True,
            )
            return None

        if not region:
            region = userdata["region"]
        region = region.upper() if region else None

        ref = parse_character_input(name)
        if ref is None:
            name, surname = split_name(name, surname)
            if not region:
                await ctx.send(
                    tr_lang(
                        lang,
                        "Bitte gib eine Region an (z. B. `EU` oder `US`).",
                        "Please specify a region (e.g. `EU` or `US`).",
                    ),
                    ephemeral=True,
                )
                return None

        await self._defer(ctx)

        if not await self._ensure_client():
            await ctx.send(self._msg_auth(lang, ctx.clean_prefix))
            return None

        if ref is not None:
            result: LookupResult = await self.resolver.resolve_ref(self.http, ref)
            if ref.get("char_id") is not None:
                who = f"ID {ref['char_id']}"
            else:
                who = f"{ref.get('char_name')} ({ref.get('char_server')})"
                region = region or ref.get("char_server")
        else:
            result = await self.resolver.resolve_name(self.http, name, surname, region)
            who = f"{normalize_name(name)} {normalize_name(surname) if surname else ''}".strip()
            who = f"{who} ({region})"

        if result.auth_error:
            await ctx.send(self._msg_auth(lang, ctx.clean_prefix))
            return None
        character = result.character
        if not character:
            msg = self._msg_not_found(lang, who)
            if result.error:
                msg += f"\nWCL API: {result.error}"[:500]
            await ctx.send(msg)
            return None

        char_id = character.get("id")
        char_ref = {"char_id": int(char_id)} if char_id is not None else (ref or {})
        if not char_ref:
            await ctx.send(self._msg_not_found(lang, who))
            return None
        return char_ref, character, self._char_title(character, surname, region)

    @staticmethod
    async def _defer(ctx: commands.Context) -> None:
        """Defer only once (rank already defers before resolving the character)."""
        if ctx.interaction is not None and ctx.interaction.response.is_done():
            return
        await ctx.defer()

    @staticmethod
    def _char_title(character: dict, surname: Optional[str], region: Optional[str]) -> str:
        cname = str(character.get("name") or "?")
        if surname and surname.lower() not in cname.lower():
            cname = f"{cname} {normalize_name(surname)}"
        if not region:
            server = character.get("server") or {}
            if isinstance(server, dict) and isinstance(server.get("region"), dict):
                region = server["region"].get("slug")
        return f"{cname} ({region.upper()})" if region else cname

    # ------------------------------------------------------------------ #
    # Dashboard
    # ------------------------------------------------------------------ #
    async def cog_load(self) -> None:
        register_dashboard(self)

    @dashboard_widget("notify_channel_set", L("Benachrichtigungskanal", "Notification Channel"), size="sm", permission="guild_member")
    async def notify_channel_set_widget(self, ctx):
        try:
            channel_id = await self.config.guild(ctx.guild).notification_channel()
            return WidgetData.kpi(value="Ja" if channel_id else "Nein", label="Benachrichtigungskanal")
        except Exception:
            return WidgetData.kpi(value="–", label="Benachrichtigungskanal")

    # --- Guild panel: notification channel ------------------------------- #
    @dashboard_panel("wcl_guild", "WarcraftLogs Forever", mount="guild_settings", permission="guild_admin")
    async def wcl_guild_panel(self, ctx):
        current = await self.config.guild(ctx.guild).notification_channel()
        ch_opts = [{"value": "", "label": "— no channel —"}] + [
            {"value": str(c.id), "label": "#" + c.name} for c in ctx.guild.text_channels
        ]
        return PanelSchema(
            fields=[
                Field.select(
                    "notification_channel", "Notification channel", ch_opts,
                    value=str(current or ""),
                ),
            ]
        )

    @wcl_guild_panel.on_submit
    async def _save_wcl_guild(self, ctx, data):
        if "notification_channel" in data:
            v = data["notification_channel"]
            await self.config.guild(ctx.guild).notification_channel.set(int(v) if v else None)
        return SubmitResult.ok("Gespeichert.")

    @dashboard_panel("language", L("Sprache", "Language"), mount="guild_settings", permission="guild_admin", order=99)
    async def language_panel(self, ctx):
        return PanelSchema(
            description=tr(ctx, "Sprache der Bot-Ausgaben für diesen Server.", "Output language for this server."),
            fields=[
                Field.select("language", L("Sprache", "Language"),
                    [{"value": "de-DE", "label": "Deutsch"}, {"value": "en-US", "label": "English"}],
                    value=str(await self.config.guild(ctx.guild).language()), reload_on_change=True),
            ],
        )

    @language_panel.on_submit
    async def _language_save(self, ctx, data):
        if "language" in data:
            await self.config.guild(ctx.guild).language.set("en-US" if data.get("language") == "en-US" else "de-DE")
        return SubmitResult.ok(tr(ctx, "Gespeichert.", "Saved."))

    # --- Guild panel (per user): WCL Forever character -------------------- #
    @dashboard_panel("wcl_char", L("Mein WCL-Forever-Charakter", "My WCL Forever Character"), mount="guild_settings", permission="guild_member")
    async def wcl_char_panel(self, ctx):
        u = self.config.user_from_id(int(ctx.user.id))
        region_opts = [{"value": r, "label": r} for r in REGIONS]
        return PanelSchema(
            description=tr(
                ctx,
                "WoW: Forever hat keine Realms. Name + Nachname, oder WCL-Link / Charakter-ID als Name.",
                "WoW: Forever has no realms. Name + surname, or a WCL link / character ID as name.",
            ),
            fields=[
                Field.text("charname", L("Charaktername", "Character name"), value=str(await u.charname() or "")),
                Field.text("surname", L("Nachname", "Surname"), value=str(await u.surname() or "")),
                Field.select("region", "Region", region_opts, value=str(await u.region() or "")),
            ]
        )

    @wcl_char_panel.on_submit
    async def _save_wcl_char(self, ctx, data):
        u = self.config.user_from_id(int(ctx.user.id))
        if "charname" in data:
            v = str(data["charname"]).strip()
            await u.charname.set(v or None)
        if "surname" in data:
            v = str(data["surname"]).strip()
            await u.surname.set(v or None)
        if "region" in data:
            v = str(data["region"]).strip().upper()
            await u.region.set(v or None)
        return SubmitResult.ok("Gespeichert.")

    # --- Global panel (bot owner): shared Warcraft Logs API key ---------- #
    # GLOBAL scope -> the SAME panel is registered by the classic and retail
    # cogs; all read/write the SHARED "warcraftlogs" client_id/client_secret.
    # Only the bearer is per-cog (Forever: own Config).
    @dashboard_panel("wcl_apikey", L("Warcraft Logs API", "Warcraft Logs API"), scope="global", mount="bot_settings", permission="bot_owner")
    async def wcl_apikey_panel(self, ctx):
        tok = await self.bot.get_shared_api_tokens("warcraftlogs")
        return PanelSchema(
            description=tr(
                ctx,
                "API-Zugang (gilt für Classic, Retail UND Forever). Erstelle einen Client unter https://www.warcraftlogs.com/api/clients/",
                "API access (applies to Classic, Retail AND Forever). Create a client at https://www.warcraftlogs.com/api/clients/",
            ),
            fields=[
                Field.text("client_id", L("Client ID", "Client ID"), value=tok.get("client_id", "")),
                Field.text("client_secret", L("Client Secret", "Client Secret"), value=tok.get("client_secret", "")),
            ],
        )

    @wcl_apikey_panel.on_submit
    async def _wcl_apikey_save(self, ctx, data):
        await self.bot.set_shared_api_tokens(
            "warcraftlogs",
            client_id=str(data.get("client_id", "")).strip(),
            client_secret=str(data.get("client_secret", "")).strip(),
        )
        return SubmitResult.ok(tr(ctx, "Gespeichert.", "Saved."))

    def cog_unload(self) -> None:
        unregister_dashboard(self)
        if self.http is not None:
            self.bot.loop.create_task(self.http.close())

    async def red_get_data_for_user(self, **kwargs):
        return {}

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord", "owner", "user", "user_strict"],
        user_id: int,
    ):
        await self.config.user_from_id(user_id).clear()

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #
    @commands.hybrid_group(
        name="warcraftlogs-forever",
        aliases=["wcl-forever"],
        description="Retrieve WoW: Forever character information from WarcraftLogs.",
        extras={"i18n_desc": {
            "de-DE": "WoW-Forever-Charakterinformationen von WarcraftLogs abrufen.",
            "en-US": "Retrieve WoW: Forever character information from WarcraftLogs.",
        }},
    )
    async def warcraftlogs(self, ctx: commands.Context):
        """Retrieve WoW: Forever character information from WarcraftLogs."""
        pass

    @commands.bot_has_permissions(embed_links=True)
    @app_commands.describe(
        name="Character name, WCL character link or WCL character ID",
        surname="Character surname (WoW: Forever has no realms)",
        region="Region (EU, US, ...)",
    )
    @warcraftlogs.command(
        description="Fetch a character's gear from the latest log entry that includes gear data.",
        extras={"i18n_desc": {
            "de-DE": "Die Ausrüstung eines Charakters aus dem letzten Log mit Ausrüstungsdaten abrufen.",
            "en-US": "Fetch a character's gear from the latest log entry that includes gear data.",
        }},
    )
    async def gear(
        self,
        ctx,
        name: Optional[str] = None,
        surname: Optional[str] = None,
        region: Optional[str] = None,
    ):
        """
        Fetch a character's gear.

        Examples:
        [p]wcl-forever gear Name Surname EU
        [p]wcl-forever gear "Name Surname" US
        [p]wcl-forever gear https://forever.warcraftlogs.com/character/id/12345
        [p]wcl-forever gear 12345

        This is provided from the last log entry for a user that includes gear data.
        Not every log has gear data.
        """
        if ctx.interaction:
            # There is no contextual locale for interactions, so we need to set it manually
            # (This is probably a bug in Red, remove this when it's fixed)
            await set_contextual_locales_from_guild(self.bot, ctx.guild)

        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"

        resolved = await self._resolve_character(ctx, lang, name, surname, region)
        if resolved is None:
            return
        char_ref, character, title = resolved

        # Get the user's last raid encounters
        encounters = await self.http.get_last_encounter(char_ref)

        if encounters is None:
            return await ctx.send(_("The bearer token was invalidated for some reason."))
        if encounters is False:
            return await ctx.send(self._msg_not_found(lang, title))
        if encounters.get("auth"):
            return await ctx.send(self._msg_auth(lang, ctx.clean_prefix))
        error = encounters.get("error", None)
        if error:
            return await ctx.send(f"WCL API Error: {error}")

        encounter_ids = [e for e in encounters.get("ids", []) if e]
        latest = encounters.get("latest") or 0
        if latest:
            encounter_ids = [latest] + [e for e in encounter_ids if e != latest]
        if not encounter_ids:
            return await ctx.send(
                _("No gear for {name} found in the last report.").format(name=title)
            )

        gear = None
        best_rank = None
        for encounter in encounter_ids:
            char_data = await self.http.get_gear(char_ref, encounter)
            if char_data is None:
                return await ctx.send(_("The bearer token was invalidated for some reason."))
            if char_data is False:
                return await ctx.send(self._msg_not_found(lang, title))
            if not isinstance(char_data, dict):
                continue
            if char_data.get("auth"):
                return await ctx.send(self._msg_auth(lang, ctx.clean_prefix))
            if char_data.get("error"):
                if encounter == encounter_ids[0]:
                    return await ctx.send(f"WCL API Error: {char_data['error']}")
                continue
            rankings = char_data.get("encounterRankings")
            ranks = rankings.get("ranks") if isinstance(rankings, dict) else None
            if not ranks:
                continue
            sorted_by_time = sorted(
                ranks,
                key=lambda k: ((k.get("report") or {}).get("startTime") or 0),
                reverse=True,
            )
            if sorted_by_time[0].get("gear"):
                best_rank = sorted_by_time[0]
                gear = best_rank["gear"]
                break

        if gear is None or best_rank is None:
            return await ctx.send(
                _("No gear for {name} found in the last report.").format(name=title)
            )

        item_list = []
        item_ilevel = 0
        item_count = 0

        for idx, item in enumerate(gear):
            # Determine slot name based on index
            slot_name = SLOT_ORDER[idx] if idx < len(SLOT_ORDER) else f"Slot {idx}"

            if not item or item.get("id", 0) == 0:
                # empty/missing item (e.g. no shoulders worn)
                item_list.append(f"{slot_name}: {tr_lang(lang, '<kein Item>', '<no item>')}")
                continue

            rarity = self._get_rarity(item)
            item_ilevel_entry = item.get("itemLevel")
            try:
                if item_ilevel_entry and int(item_ilevel_entry) > 5:
                    item_ilevel += int(item_ilevel_entry)
                    item_count += 1
            except (TypeError, ValueError):
                pass

            item_list.append(
                f"{slot_name}: {rarity} "
                f"[{item.get('name', item['id'])}]({WOWHEAD_ITEM_URL.format(item['id'])}) "
                f"({item_ilevel_entry})"
            )

            # Enchants & gems. No Forever enchant name table exists, so the WCL
            # name is used when present, otherwise the raw enchant id.
            # (Field names like permanentEnchantName are read defensively.)
            sub_lines = []
            perm_enchant = item.get("permanentEnchant")
            if perm_enchant:
                label = item.get("permanentEnchantName") or f"#{perm_enchant}"
                sub_lines.append(f"{tr_lang(lang, 'Verzauberung', 'Enchant')}: {label}")
            for gem in item.get("gems") or []:
                gem_id = gem.get("id") if isinstance(gem, dict) else None
                if gem_id:
                    sub_lines.append(
                        f"{tr_lang(lang, 'Edelstein', 'Gem')}: "
                        f"[{gem_id}]({WOWHEAD_ITEM_URL.format(gem_id)})"
                    )
            temp_enchant = item.get("temporaryEnchant")
            if temp_enchant:
                label = item.get("temporaryEnchantName") or f"#{temp_enchant}"
                sub_lines.append(
                    f"{tr_lang(lang, 'Temporäre Verzauberung', 'Temporary enchant')}: {label}"
                )
            for i, line in enumerate(sub_lines):
                symbol = "└" if i == len(sub_lines) - 1 else "├"
                item_list.append(f"`{symbol}──` {line}")

        if item_count > 0:
            avg_ilevel = "{:g}".format(round(item_ilevel / item_count, 1))
        else:
            avg_ilevel = _("Unknown (not present in log data from the API)")

        guild_name = (best_rank.get("guild") or {}).get("name", None)
        ilvl = _("Average Item Level: {avg_ilevel}\n").format(avg_ilevel=avg_ilevel)
        encounter_spec = best_rank.get("spec", None)
        spec = _("Encounter spec: {encounter_spec}\n").format(encounter_spec=encounter_spec)
        report_code = (best_rank.get("report") or {}).get("code", "")
        gear_data = _("Gear data pulled from {report_url}\n").format(
            report_url=WCL_URL.format(report_code)
        )
        log_date = _("Log Date/Time: {datetime} UTC").format(
            datetime=self._time_convert(best_rank.get("startTime") or 0)
        )

        if ctx.guild is None or ctx.channel.permissions_for(ctx.guild.me).embed_links:
            embed = discord.Embed()
            embed_title = title + (f"\n{guild_name}" if guild_name else "")
            embed.title = embed_title[:256]
            embed.url = f"{WCL_BASE}/character/id/{char_ref['char_id']}" if char_ref.get("char_id") else None
            embed.description = "\n".join(item_list)[:4096]
            embed.colour = await ctx.embed_color()
            embed.set_footer(text=f"{spec}{ilvl}{gear_data}{log_date}")
            await ctx.send(embed=embed)
        else:
            text_title = title + (f" - {guild_name}" if guild_name else "")
            msg = "{}\n{}{}{}{}\n\n{}".format(
                text_title, spec, ilvl, gear_data, log_date, "\n".join(item_list)
            )
            await ctx.send(msg[:2000])

    @gear.autocomplete("region")
    async def warcraftlogs_gear_region_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return self._region_choices(current)

    @commands.bot_has_permissions(embed_links=True)
    @warcraftlogs.command(
        description="Show a character's raid rank overview (best/median performance per encounter).",
        extras={"i18n_desc": {
            "de-DE": "Rang-Übersicht eines Charakters anzeigen (beste/mittlere Leistung pro Encounter).",
            "en-US": "Show a character's raid rank overview (best/median performance per encounter).",
        }},
    )
    @app_commands.describe(
        name="Character name, WCL character link or WCL character ID",
        surname="Character surname (WoW: Forever has no realms)",
        region="Region (EU, US, ...)",
        zone="The raid zone (live list from Warcraft Logs)",
        difficulty="Raid difficulty",
    )
    async def rank(
        self,
        ctx,
        name: Optional[str] = None,
        surname: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        difficulty: Optional[str] = None,
    ):
        """
        Character rank overview.

        Examples:
        [p]wcl-forever rank Name Surname EU
        [p]wcl-forever rank Name Surname EU "<zone name or id>"
        [p]wcl-forever rank 12345

        Zones are loaded live from forever.warcraftlogs.com (use the slash
        command autocomplete, or pass the zone name/id in quotes).
        """
        if ctx.interaction:
            # There is no contextual locale for interactions, so we need to set it manually
            # (This is probably a bug in Red, remove this when it's fixed)
            await set_contextual_locales_from_guild(self.bot, ctx.guild)

        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"

        # Defer first: the client setup and the zone list may hit the network.
        await self._defer(ctx)
        if not await self._ensure_client():
            return await ctx.send(self._msg_auth(lang, ctx.clean_prefix))
        # Fetch the live zone list (cached).
        zones = await self._get_zones()
        zones_by_id: Dict[str, dict] = {str(z["id"]): z for z in zones}
        zones_by_name: Dict[str, dict] = {str(z.get("name", "")).lower(): z for z in zones}

        # someone has their data saved, so they are just trying
        # to look up a zone for themselves
        if (
            name
            and parse_character_input(name) is None
            and (name in zones_by_id or name.lower() in zones_by_name)
        ):
            zone, name, surname = name, None, None

        resolved = await self._resolve_character(ctx, lang, name, surname, region)
        if resolved is None:
            return
        char_ref, character, title = resolved

        # Zones may have been empty before the client existed: retry once.
        if not zones:
            zones = await self._get_zones(force=True)
            zones_by_id = {str(z["id"]): z for z in zones}
            zones_by_name = {str(z.get("name", "")).lower(): z for z in zones}

        # Resolve zone (autocomplete passes the zone id, prefix users may pass a name).
        zone_obj = None
        if zone:
            zone_obj = zones_by_id.get(str(zone)) or zones_by_name.get(str(zone).lower())
            if zone_obj is None:
                return await ctx.send(
                    tr_lang(
                        lang,
                        f"Zone `{zone}` ist auf forever.warcraftlogs.com unbekannt.",
                        f"Zone `{zone}` is unknown on forever.warcraftlogs.com.",
                    )
                )
        zone_id = int(zone_obj["id"]) if zone_obj else None
        zone_id_to_name = zone_obj.get("name") if zone_obj else None

        difficulty_id = self._resolve_difficulty(difficulty, zone_obj, zones)

        data = None
        if zone_id is None:
            # Probe the newest zones first (highest id) until one has parse data,
            # as no specific zone was requested. Frozen zones are skipped unless
            # all zones are frozen.
            newest = sorted(zones, key=lambda z: int(z["id"]), reverse=True)
            candidates = ([z for z in newest if not z.get("frozen")] or newest)[:ZONE_PROBE_LIMIT]
            if not candidates:
                return await ctx.send(
                    tr_lang(
                        lang,
                        "Die Zonenliste von forever.warcraftlogs.com konnte nicht geladen werden.",
                        "Could not load the zone list from forever.warcraftlogs.com.",
                    )
                )
            for zone_candidate in candidates:
                data = await self.http.get_overview(char_ref, int(zone_candidate["id"]), difficulty_id)
                err = self._overview_error(data, lang, ctx.clean_prefix, title)
                if err:
                    if data and data.get("error") and not data.get("auth"):
                        # Zone may not support rankings (e.g. dungeons): keep probing.
                        continue
                    return await ctx.send(err)
                zr = self._zone_rankings(data)
                if zr.get("bestPerformanceAverage") is not None:
                    break
        else:
            data = await self.http.get_overview(char_ref, zone_id, difficulty_id)
            err = self._overview_error(data, lang, ctx.clean_prefix, title)
            if err:
                return await ctx.send(err)

        if not isinstance(data, dict) or data.get("error"):
            return await ctx.send(_("Nothing at all found for this player."))

        # embed and data setup
        zws = "\N{ZERO WIDTH SPACE}"

        char_data = self._zone_rankings(data)
        if not char_data:
            msg = _(
                "Something went terribly wrong while trying to "
                "access the zone rankings for this character."
            )
            return await ctx.send(msg)

        if not char_data.get("bestPerformanceAverage", None):
            return (
                await ctx.send(
                    _("Nothing found for {zone_name} for this player.").format(
                        zone_name=zone_id_to_name
                    )
                )
                if zone_id
                else await ctx.send(_("Nothing at all found for this player."))
            )

        result_zone = zones_by_id.get(str(char_data.get("zone"))) or zone_obj
        difficulty_name = self._difficulty_name(char_data.get("difficulty"), result_zone, zones)
        zone_name = (result_zone or {}).get("name") or str(char_data.get("zone", "?"))
        zone_header = f"⫷ {difficulty_name} {zone_name} ⫸".replace("  ", " ").center(50, " ")

        embed = discord.Embed()
        embed.title = title[:256]
        embed.url = f"{WCL_BASE}/character/id/{char_ref['char_id']}" if char_ref.get("char_id") else None
        embed.colour = await ctx.embed_color()

        # perf averages
        embed.add_field(name=zws, value=box(zone_header, lang="fix"), inline=False)

        pf_avg = "{:.1f}".format(char_data["bestPerformanceAverage"])
        pf_avg = self._get_color(float(pf_avg))
        embed.add_field(name=_("Best Perf. Avg"), value=pf_avg, inline=True)
        md_raw = char_data.get("medianPerformanceAverage")
        md_avg = self._get_color(float("{:.1f}".format(md_raw))) if md_raw is not None else "-"
        embed.add_field(name=_("Median Perf. Avg"), value=md_avg, inline=True)

        # perf avg filler space
        embed.add_field(name=zws, value=zws, inline=True)

        # table setup
        table = BeautifulTable(default_alignment=ALIGN_LEFT, maxwidth=500)
        table.set_style(BeautifulTable.STYLE_COMPACT)
        table.columns.header = [
            _("Name"),
            _("Best %"),
            _("Spec"),
            _("DPS"),
            _("Kills"),
            _("Fastest"),
            _("Med %"),
            _("AS Pts"),
            _("AS Rank"),
        ]

        # add rankings per encounter to table
        for encounter in char_data.get("rankings") or []:
            all_stars = encounter.get("allStars")
            enc_details = encounter.get("encounter") or {}
            best_amount = encounter.get("bestAmount") or 0
            best_amt = self.humanize_dps(best_amount) if best_amount else "-"
            median_pct = (
                "{:.1f}".format(encounter["medianPercent"]) if encounter.get("medianPercent") else "-"
            )
            rank_pct = (
                "{:.1f}".format(encounter["rankPercent"]) if encounter.get("rankPercent") else "-"
            )
            fastest_kill_tup = self._dynamic_time((encounter.get("fastestKill") or 0) / 1000)

            if fastest_kill_tup == (0, 0):
                fastest_kill = "-"
            else:
                fastest_kill = f"{fastest_kill_tup[0]}:{fastest_kill_tup[1]:02d}"

            table.rows.append(
                (
                    enc_details.get("name", None),
                    rank_pct,
                    encounter.get("spec") or "-",
                    best_amt,
                    encounter.get("totalKills", 0),
                    fastest_kill,
                    median_pct,
                    all_stars.get("points", None) if all_stars else "-",
                    all_stars.get("rank", None) if all_stars else "-",
                )
            )

        # all stars
        all_stars = char_data.get("allStars") or []
        if all_stars:
            section_name = _("⫷ Expansion All Stars ⫸").center(50, " ")
            embed.add_field(name=zws, value=box(section_name, lang="Prolog"), inline=False)
            for item in all_stars:
                msg = f"**{item.get('spec', '-')}**\n"
                msg += _("Points:\n`{points}`\n").format(points=item.get("points"))
                msg += _("Rank:\n`{rank}`\n").format(rank=item.get("rank"))
                if item.get("rankPercent") is not None:
                    rank_percent = "{:.1f}".format(item["rankPercent"])
                    msg += f"{self._get_color(float(rank_percent), '%')}\n"
                embed.add_field(name=zws, value=msg, inline=True)

            # all stars filler space
            nearest_multiple = 3 * math.ceil(len(all_stars) / 3)
            for _1 in range(nearest_multiple - len(all_stars)):
                embed.add_field(name=zws, value=zws, inline=True)

        # table time
        table_image = await self._make_table_image(str(table))
        image_file = discord.File(fp=table_image, filename="table_image.png")
        embed.set_image(url=f"attachment://{image_file.filename}")

        await ctx.send(file=image_file, embed=embed)

    @rank.autocomplete("region")
    async def warcraftlogs_rank_region_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return self._region_choices(current)

    @rank.autocomplete("zone")
    async def warcraftlogs_rank_zone_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete the zone from the live (cached) WCL zone list.

        Choice value is the zone id (as a string), newest zones first."""
        zones = await self._get_zones()
        current_lower = (current or "").lower()
        choices: List[app_commands.Choice[str]] = []
        for z in sorted(zones, key=lambda z: int(z["id"]), reverse=True):
            zname = str(z.get("name", z["id"]))
            if current_lower and current_lower not in zname.lower():
                continue
            choices.append(app_commands.Choice(name=zname[:100], value=str(z["id"])))
            if len(choices) >= 25:
                break
        return choices

    @rank.autocomplete("difficulty")
    async def warcraftlogs_rank_difficulty_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete difficulties from the live zone data.

        Uses the selected zone's difficulties if a zone was picked, otherwise
        the union of all zones. Choice value is the WCL difficulty id."""
        zones = await self._get_zones()
        selected = str(getattr(interaction.namespace, "zone", "") or "")
        zone_obj = next((z for z in zones if str(z["id"]) == selected), None)
        current_lower = (current or "").lower()
        choices: List[app_commands.Choice[str]] = []
        for diff_id, diff_name in self._difficulties_of(zone_obj, zones).items():
            if current_lower and current_lower not in diff_name.lower():
                continue
            choices.append(app_commands.Choice(name=diff_name[:100], value=str(diff_id)))
            if len(choices) >= 25:
                break
        return choices

    # ------------------------------------------------------------------ #
    # rank helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _region_choices(current: str) -> List[app_commands.Choice[str]]:
        current_upper = (current or "").upper()
        return [
            app_commands.Choice(name=r, value=r)
            for r in REGIONS
            if not current_upper or current_upper in r
        ][:25]

    @staticmethod
    def _difficulties_of(zone_obj: Optional[dict], zones: list) -> Dict[int, str]:
        """id -> name from the live zone data (read defensively)."""
        source = [zone_obj] if zone_obj else zones
        out: Dict[int, str] = {}
        for z in source:
            for d in (z or {}).get("difficulties") or []:
                try:
                    out.setdefault(int(d["id"]), str(d.get("name") or d["id"]))
                except (KeyError, TypeError, ValueError):
                    continue
        return out

    def _resolve_difficulty(self, difficulty: Optional[str], zone_obj: Optional[dict], zones: list) -> int:
        """Map user input (id or name) to a WCL difficulty id; 0 = API default."""
        if not difficulty:
            return 0
        text = str(difficulty).strip()
        if text.isdigit():
            return int(text)
        for diff_id, diff_name in self._difficulties_of(zone_obj, zones).items():
            if diff_name.lower() == text.lower():
                return diff_id
        return 0

    def _difficulty_name(self, diff_id, zone_obj: Optional[dict], zones: list) -> str:
        if diff_id is None:
            return ""
        try:
            diff_id = int(diff_id)
        except (TypeError, ValueError):
            return str(diff_id)
        names = self._difficulties_of(zone_obj, zones)
        # Fall back to the raw id when the zone data has no name for it.
        return names.get(diff_id) or str(diff_id)

    @staticmethod
    def _zone_rankings(data) -> dict:
        try:
            zr = data["data"]["characterData"]["character"]["zoneRankings"]
        except (KeyError, TypeError):
            return {}
        return zr if isinstance(zr, dict) else {}

    def _overview_error(self, data, lang: str, prefix: str, title: str) -> Optional[str]:
        """Return a user-facing error message for a get_overview result, or None."""
        if data is None:
            return _("The bearer token was invalidated for some reason.")
        if data.get("auth"):
            return self._msg_auth(lang, prefix)
        if data.get("error"):
            return f"WCL API Error: {data['error']}"
        try:
            if not data["data"]["characterData"]["character"]:
                return self._msg_not_found(lang, title)
        except (KeyError, TypeError):
            return _("Nothing at all found for this player.")
        return None

    @staticmethod
    def humanize_dps(dps: int | float) -> str:
        if dps >= 1000000:
            return f"{round(dps / 1000000)}M"
        elif dps >= 10000:
            return f"{round(dps / 1000)}k"
        else:
            return str(dps)

    @commands.hybrid_group(
        name="wclset-forever",
        description="Commands for setting up WCL (Forever) settings.",
        extras={"i18n_desc": {
            "de-DE": "Befehle zum Einrichten der WCL-(Forever)-Einstellungen.",
            "en-US": "Commands for setting up WCL (Forever) settings.",
        }},
    )
    async def wclset(self, ctx: commands.Context):
        """Commands for setting up WCL (Forever) settings."""
        pass

    @wclset.command(
        name="charname",
        description="Set your character's name (or a WCL character link / ID).",
        extras={"i18n_desc": {
            "de-DE": "Den Namen deines Charakters festlegen (oder WCL-Charakter-Link / -ID).",
            "en-US": "Set your character's name (or a WCL character link / ID).",
        }},
    )
    @app_commands.describe(charname="Your character's name, WCL character link or WCL character ID")
    async def wclset_charname(self, ctx, *, charname: str):
        """Set your character's name (or a WCL character link / ID)."""
        charname = charname.strip()
        await self.config.user(ctx.author).charname.set(charname)
        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"
        await ctx.send(
            tr_lang(
                lang,
                f"Dein Charaktername wurde auf {charname} gesetzt.",
                f"Your character name was set to {charname}.",
            )
        )

    @wclset.command(
        name="surname",
        description="Set your character's surname (WoW: Forever has no realms).",
        extras={"i18n_desc": {
            "de-DE": "Den Nachnamen deines Charakters festlegen (WoW: Forever hat keine Realms).",
            "en-US": "Set your character's surname (WoW: Forever has no realms).",
        }},
    )
    @app_commands.describe(surname="Your character's surname (leave empty to clear)")
    async def wclset_surname(self, ctx, *, surname: Optional[str] = None):
        """Set your character's surname (WoW: Forever has no realms)."""
        surname = surname.strip() if surname else None
        await self.config.user(ctx.author).surname.set(surname or None)
        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"
        if surname:
            msg = tr_lang(
                lang,
                f"Dein Nachname wurde auf {normalize_name(surname)} gesetzt.",
                f"Your surname was set to {normalize_name(surname)}.",
            )
        else:
            msg = tr_lang(lang, "Dein Nachname wurde entfernt.", "Your surname was cleared.")
        await ctx.send(msg)

    @wclset.command(
        name="region",
        description="Set your region.",
        extras={"i18n_desc": {
            "de-DE": "Deine Region festlegen.",
            "en-US": "Set your region.",
        }},
    )
    @app_commands.describe(region="Your region (EU, US, ...)")
    async def wclset_region(self, ctx, region: str):
        """Set your region."""
        if region.upper() not in REGIONS:
            return await ctx.send(
                _("Valid regions are: {valid_regions}").format(
                    valid_regions=humanize_list(REGIONS)
                )
            )
        await self.config.user(ctx.author).region.set(region.upper())
        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"
        await ctx.send(
            tr_lang(
                lang,
                f"Deine Region wurde auf {region.upper()} gesetzt.",
                f"Your region was set to {region.upper()}.",
            )
        )

    @wclset_region.autocomplete("region")
    async def wclset_region_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return self._region_choices(current)

    @wclset.command(
        name="channel",
        hidden=True,
        description="Set the channel where WCL updates will be sent.",
        extras={"i18n_desc": {
            "de-DE": "Den Kanal festlegen, in den WCL-Updates gesendet werden.",
            "en-US": "Set the channel where WCL updates will be sent.",
        }},
    )
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.describe(channel="The channel where WCL updates will be sent")
    async def wclset_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel where WCL updates will be sent."""
        await self.config.guild(ctx.guild).notification_channel.set(channel.id)
        await ctx.send(
            _("WCL updates will now be sent to {channel}.").format(channel=channel.mention)
        )

    @wclset.command(
        name="settings",
        description="Show your current settings.",
        extras={"i18n_desc": {
            "de-DE": "Deine aktuellen Einstellungen anzeigen.",
            "en-US": "Show your current settings.",
        }},
    )
    @commands.guild_only()
    @app_commands.describe(user="The user whose settings to show (defaults to yourself)")
    async def wclset_settings(self, ctx, user: discord.User = None):
        """Show your current settings."""
        if not user:
            user = ctx.author
        userinfo = await self.config.user(user).all()
        guildinfo = await self.config.guild(ctx.guild).all()

        msg = _("[Settings for {guild}]\n").format(guild=ctx.guild.name)
        notification_channel = ctx.guild.get_channel(guildinfo["notification_channel"] or 0)
        # Guard against an unset or deleted notification channel.
        msg += _("Notification channel: {channel}\n").format(
            channel=notification_channel.name if notification_channel else "None"
        )
        msg += "\n"

        msg += _("[Settings for {user}]\n").format(user=user.display_name)
        charname = userinfo["charname"] or "None"
        surname = normalize_name(userinfo["surname"]) if userinfo["surname"] else "None"
        regionname = userinfo["region"].upper() if userinfo["region"] else "None"
        msg += f"Character: {charname}\nSurname: {surname}\nRegion: {regionname}\n\n"

        msg += _("[Bot Permissions Needed]\n")
        if ctx.guild.me.guild_permissions.embed_links:
            msg += _("[X] Embed Links permissions\n")
        else:
            msg += _("[ ] I need Embed Links permissions\n")

        await ctx.send(box(msg, lang="ini"))

    @wclset.command(
        name="apikey",
        description="Instructions for setting the API key.",
        extras={"i18n_desc": {
            "de-DE": "Anleitung zum Festlegen des API-Schlüssels.",
            "en-US": "Instructions for setting the API key.",
        }},
    )
    @checks.is_owner()
    async def wclset_apikey(self, ctx):
        """Instructions for setting the api key."""
        msg = _(
            "Set your API key by adding it to Red's API key storage.\n"
            "Get a key from <https://www.warcraftlogs.com> by signing up for an account, "
            "then visit your settings.\n"
            "At the bottom is a section called Web API. "
            "Click on the blue link that says `manage your V2 clients here`.\n"
            "Do NOT sign up for a v1 API key, it will not work with this cog.\n"
            "Click on Create Client. Be ready to write down your information somewhere, "
            "you cannot retrieve the secret after this.\n"
            "Enter a name (whatever you want), `https://localhost` for the redirect URL, "
            "and leave the Public Client box unchecked.\n"
            "Use `{prefix}set api warcraftlogs client_id,client-id-goes-here client_secret,"
            "client-secret-goes-here` to set your key.\n"
            "The same client is shared with the Classic and Retail Warcraft Logs cogs. "
            "This cog requests its own token from forever.warcraftlogs.com; if that site "
            "answers 401, the client is not accepted there.\n"
        ).format(prefix=ctx.clean_prefix)
        await ctx.send(msg)

    async def _make_table_image(self, table):
        image_path = str(self.path / "blank.png")
        image = Image.open(image_path)
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(self.path / "Cousine-Regular.ttf"), 20)

        x = 20
        y = 0

        text_lines = table.split("\n")
        for text_line in text_lines:
            y += 25
            draw.text((x, y), text_line, font=font, fill=(255, 255, 255, 255))

        image_object = io.BytesIO()
        image.save(image_object, format="PNG")
        image_object.seek(0)
        return image_object

    @staticmethod
    def _dynamic_time(time_elapsed):
        m, s = divmod(int(time_elapsed), 60)
        return m, s

    @staticmethod
    def _get_rarity(item):
        rarity = item.get("quality")
        if rarity == "common":
            return "⬜"
        elif rarity == "uncommon":
            return "🟩"
        elif rarity == "rare":
            return "🟦"
        elif rarity == "epic":
            return "🟪"
        elif rarity == "legendary":
            return "🟧"
        else:
            return "🔳"

    @staticmethod
    def _time_convert(time):
        time = str(time)[:10]
        # WCL timestamps are UTC epoch (ms); the output is labelled "UTC".
        return datetime.fromtimestamp(int(time), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


    def _get_color(self, number: float, bonus=""):
        if number >= 95:
            # legendary
            return self._orange(number, bonus)
        elif number > 75:
            # epic
            return self._red(number, bonus)
        elif number > 50:
            # rare
            return self._blue(number, bonus)
        elif number > 25:
            # common
            return self._green(number, bonus)
        elif number >= 0:
            # trash
            return self._grey(number, bonus)
        else:
            # someone fucked up somewhere
            return box(str(number))

    @staticmethod
    def _red(number, bonus):
        output_center = f"{str(number)}{bonus}".center(8, " ")
        text = f"[  {output_center}  ]"
        return f"{box(text, lang='css')}"

    @staticmethod
    def _orange(number, bonus):
        output_center = f"{str(number)}{bonus}".center(8, " ")
        text = f"[  {output_center}  ]"
        return f"{box(text, lang='fix')}"

    @staticmethod
    def _green(number, bonus):
        output_center = f"{str(number)}{bonus}".center(8, " ")
        text = f"[  {output_center}  ]"
        return f"{box(text, lang='py')}"

    @staticmethod
    def _blue(number, bonus):
        output_center = f"{str(number)}{bonus}".center(8, " ")
        text = f"[  {output_center}  ]"
        return f"{box(text, lang='ini')}"

    @staticmethod
    def _grey(number, bonus):
        output_center = f"{str(number)}{bonus}".center(8, " ")
        text = f"[  {output_center}  ]"
        return f"{box(text, lang='bf')}"

    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name: str, api_tokens: Mapping[str, str]):
        """
        Lifted shamelessly from GHC.
        Thanks Kowlin for this and everything else you did on this cog.

        Forever only reacts when the shared client_id/client_secret change.
        Bearer-only updates of the shared "warcraftlogs" tokens (older classic
        cog versions stored their bearer there) are ignored, because the
        Forever bearer is issued by forever.warcraftlogs.com and kept in this
        cog's own Config.
        """
        if service_name != "warcraftlogs":
            return
        creds = await self._read_client_creds(api_tokens)
        if creds == self._client_creds:
            return
        self._client_creds = creds
        # New credentials -> request a new bearer (serialized by the token
        # lock) and swap the session; the old session is closed afterwards.
        await self._refresh_bearer(force=True)

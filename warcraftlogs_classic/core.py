# Most of the source of this file for the actual API mechanics can be found at:
# https://github.com/Kowlin/GraphQL-WoWLogs/blob/master/wowlogs/core.py

import asyncio
import io
import logging
import math
import time
from datetime import datetime, timezone
from typing import Dict, List, Literal, Mapping, Optional, Tuple
from .wclEnums import SLOT_ORDER_MOP

import discord
from beautifultable import ALIGN_LEFT, BeautifulTable
from discord import app_commands
from PIL import Image, ImageDraw, ImageFont
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.data_manager import bundled_data_path
from redbot.core.i18n import Translator, cog_i18n, set_contextual_locales_from_guild
from redbot.core.utils.chat_formatting import box, humanize_list

from .autocomplete import REALMS, REGIONS
from .pdc_dashboard import (
    dashboard_widget, dashboard_panel,
    WidgetData, PanelSchema, Field, SubmitResult,
    register_dashboard, unregister_dashboard,
    L, tr, tr_lang,
)
from .enchantid import ENCHANT_ID
from .encounterid import DIFFICULTIES, ZONE_ALIASES
from .http import WoWLogsClient, generate_bearer

_ = Translator("WarcraftLogsClassic", __file__)
log = logging.getLogger("red.karlo-cogs.warcraftlogs")

WCL_URL = "https://classic.warcraftlogs.com/reports/{}"

# How long (seconds) to cache the live zone list before re-fetching.
ZONES_CACHE_TTL = 3600
# Max number of zones probed when no zone was requested (rate-limit friendly).
ZONE_PROBE_LIMIT = 10


@cog_i18n(_)
class WarcraftLogsClassic(commands.Cog):
    """Retrieve World of Warcraft character information from WarcraftLogs."""

    def __init__(self, bot):
        self.bot: Red = bot
        self.config = Config.get_conf(self, identifier=87446677010550784, force_registration=True)
        self.http: WoWLogsClient = None
        self.path = bundled_data_path(self)

        # The bearer is stored in this cog's OWN config (NOT the shared
        # "warcraftlogs" tokens): writing it there fired the token-update
        # listeners of the retail/forever cogs and broke their sessions.
        self.config.register_global(bearer_timestamp=0, bearer=None)

        # Live zone cache (WCL zone ids are not Blizzard instance ids).
        self._zones_cache: Optional[list] = None
        self._zones_cache_time: float = 0.0

        # Serializes bearer renewal (concurrent 401s / expiry / token updates).
        self._token_lock = asyncio.Lock()
        # Last seen shared client credentials: token updates that do not change
        # them (e.g. a bearer written by another cog) are ignored.
        self._client_creds: Tuple[str, str] = ("", "")

        default_user = {
            "charname": None,
            "realm": None,
            "region": None,
        }
        default_guild = {
            "notification_channel": None,
            "language": "en-US",
        }

        self.config.register_user(**default_user)
        self.config.register_guild(**default_guild)

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
        # The bearer lives in this cog's OWN config (not the shared tokens).
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

        WCL zone IDs are site-internal and new zones appear with new content,
        so they are fetched from the API instead of being hardcoded. The result
        is cached for ``ZONES_CACHE_TTL`` seconds."""
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
    @dashboard_panel("wcl_guild", "WarcraftLogs", mount="guild_settings", permission="guild_admin")
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

    # --- Guild panel (per user): WCL character --------------------------- #
    @dashboard_panel("wcl_char", L("Mein WCL-Charakter", "My WCL Character"), mount="guild_settings", permission="guild_member")
    async def wcl_char_panel(self, ctx):
        u = self.config.user_from_id(int(ctx.user.id))
        region_opts = [{"value": r, "label": r} for r in REGIONS]
        return PanelSchema(
            fields=[
                Field.text("charname", "Character name", value=str(await u.charname() or "")),
                Field.text("realm", "Realm", value=str(await u.realm() or "")),
                Field.select("region", "Region", region_opts, value=str(await u.region() or "")),
            ]
        )

    @wcl_char_panel.on_submit
    async def _save_wcl_char(self, ctx, data):
        u = self.config.user_from_id(int(ctx.user.id))
        if "charname" in data:
            v = str(data["charname"]).strip()
            await u.charname.set(v or None)
        if "realm" in data:
            v = str(data["realm"]).strip()
            await u.realm.set(v or None)
        if "region" in data:
            v = str(data["region"]).strip().upper()
            await u.region.set(v or None)
        return SubmitResult.ok("Gespeichert.")

    # --- Global panel (bot owner): shared Warcraft Logs API key ---------- #
    # GLOBAL scope (scope="global" + mount="bot_settings") -> appears under the
    # dashboard's global owner-only settings. The SAME panel is registered by
    # the retail cog; both read/write the SHARED "warcraftlogs" tokens, so
    # setting it in either cog applies to both.
    @dashboard_panel("wcl_apikey", L("Warcraft Logs API", "Warcraft Logs API"), scope="global", mount="bot_settings", permission="bot_owner")
    async def wcl_apikey_panel(self, ctx):
        tok = await self.bot.get_shared_api_tokens("warcraftlogs")
        return PanelSchema(
            description=tr(ctx, "API-Zugang (gilt für Classic UND Retail). Erstelle einen Client unter https://www.warcraftlogs.com/api/clients/", "API access (applies to Classic AND Retail). Create a client at https://www.warcraftlogs.com/api/clients/"),
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

    @commands.hybrid_group(
        name="warcraftlogs-classic",
        aliases=["wcl-classic"],
        description="Retrieve World of Warcraft character information from WarcraftLogs.",
        extras={"i18n_desc": {
            "de-DE": "World-of-Warcraft-Charakterinformationen von WarcraftLogs abrufen.",
            "en-US": "Retrieve World of Warcraft character information from WarcraftLogs.",
        }},
    )
    async def warcraftlogs(self, ctx: commands.Context):
        """Retrieve World of Warcraft character information from WarcraftLogs."""
        pass

    @commands.bot_has_permissions(embed_links=True)
    @app_commands.describe(
        name="Character name",
        realm="Name of the realm",
    )
    @warcraftlogs.command(
        description="Fetch a character's gear from the latest log entry that includes gear data.",
        extras={"i18n_desc": {
            "de-DE": "Die Ausrüstung eines Charakters aus dem letzten Log mit Ausrüstungsdaten abrufen.",
            "en-US": "Fetch a character's gear from the latest log entry that includes gear data.",
        }},
    )
    async def gear(self, ctx, name: str, *, realm: str):
        """
        Fetch a character's gear.

        Examples:
        [p]getgear Username Draenor:EU
        [p]getgear Username Alterac-Mountains:US

        This is provided from the last log entry for a user that includes gear data.
        Not every log has gear data.
        Enchants can be shown - if the log provides them.
        """
        if ctx.interaction:
            # There is no contextual locale for interactions, so we need to set it manually
            # (This is probably a bug in Red, remove this when it's fixed)
            await set_contextual_locales_from_guild(self.bot, ctx.guild)

        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"

        userdata = await self.config.user(ctx.author).all()

        region = None
        if realm:
            try:
                realm, region = realm.split(sep=":", maxsplit=1)
            except ValueError:
                # No ":" in the input: treat it as a bare realm name and
                # fall back to the user's saved region below.
                pass

        if not name:
            name = userdata["charname"]
            if not name:
                return await ctx.send(
                    _("Please specify a character name with this command."),
                    ephemeral=True,
                )
        if not realm:
            realm = userdata["realm"]
            if not realm:
                return await ctx.send(
                    _("Please specify a realm name with this command."), ephemeral=True
                )
        if not region:
            region = userdata["region"]
            if not region:
                return await ctx.send(
                    _("Please specify a region like `Realm:EU` with this command."),
                    ephemeral=True,
                )

        name = name.title()
        realm = realm.title().replace(" ", "-")
        region = region.upper()

        await ctx.defer()

        if not await self._ensure_client():
            return await ctx.send(self._msg_auth(ctx.clean_prefix))

        # Get the user's last raid encounters
        encounters = await self.http.get_last_encounter(name, realm, region)

        if encounters is None:
            return await ctx.send(_("The bearer token was invalidated for some reason."))

        if encounters is False:
            # the user wasn't found on the API.
            return await ctx.send(_("{name} wasn't found on the API.").format(name=name))

        if encounters.get("auth"):
            return await ctx.send(self._msg_auth(ctx.clean_prefix))
        error = encounters.get("error", None)
        if error:
            return await ctx.send(f"WCL API Error: {error}")

        # Latest boss kill first, then the other kills of the last report.
        encounter_ids = [e for e in encounters.get("ids", []) if e]
        latest = encounters.get("latest") or 0
        if latest:
            encounter_ids = [latest] + [e for e in encounter_ids if e != latest]
        if not encounter_ids:
            return await ctx.send(
                _("No boss kills found in the last report of {name}.").format(name=name)
            )

        gear = None
        best_rank = None
        for encounter in encounter_ids:
            char_data = await self.http.get_gear(name, realm, region, encounter)
            if char_data is None:
                return await ctx.send(_("The bearer token was invalidated for some reason."))
            if char_data is False:
                return await ctx.send(_("{name} wasn't found on the API.").format(name=name))
            if not isinstance(char_data, dict):
                continue
            if char_data.get("auth"):
                return await ctx.send(self._msg_auth(ctx.clean_prefix))
            if char_data.get("error"):
                if encounter == encounter_ids[0]:
                    return await ctx.send(f"WCL API Error: {char_data['error']}")
                continue
            # Ensure this is the encounter that has gear listed.
            # IF it's not, we're moving on with the other encounters.
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
                _("No gear for {name} found in the last report.").format(name=name)
            )

        item_list = []
        item_ilevel = 0
        item_count = 0

        for idx, item in enumerate(gear):
            # Determine slot name based on index
            slot_name = SLOT_ORDER_MOP[idx] if idx < len(SLOT_ORDER_MOP) else f"Slot {idx}"

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

            # Item-Zeile
            item_list.append(
                f"{slot_name}: {rarity} "
                f"[{item.get('name', item['id'])}](https://wowhead.com/mop-classic/item={item['id']}) "
                f"({item_ilevel_entry})"
            )

            # Enchants & gems. ENCHANT_ID keys are strings while WCL sends ints,
            # so the lookup uses str(id). Unknown ids are not shown (as before).
            sub_lines = []
            perm_enchant = item.get("permanentEnchant")
            if perm_enchant:
                label = ENCHANT_ID.get(str(perm_enchant)) or item.get("permanentEnchantName")
                if label:
                    sub_lines.append(label)
            for gem in item.get("gems") or []:
                gem_id = gem.get("id") if isinstance(gem, dict) else None
                label = ENCHANT_ID.get(str(gem_id)) if gem_id else None
                if label:
                    sub_lines.append(label)
            temp_enchant = item.get("temporaryEnchant")
            if temp_enchant:
                label = ENCHANT_ID.get(str(temp_enchant)) or item.get("temporaryEnchantName")
                if label:
                    sub_lines.append(label)
            for i, line in enumerate(sub_lines):
                symbol = "└" if i == len(sub_lines) - 1 else "├"
                item_list.append(f"`{symbol}──` {line}")

        if item_count > 0:
            avg_ilevel = "{:g}".format(round(item_ilevel / item_count, 1))
        else:
            avg_ilevel = _("Unknown (not present in log data from the API)")

        title = f"{name.title()} - {realm.title()} ({region.upper()})"
        guild_name = (best_rank.get("guild") or {}).get("name", None)
        ilvl = _("Average Item Level: {avg_ilevel}\n").format(avg_ilevel=avg_ilevel)
        encounter_spec = best_rank.get("spec", None)
        spec = _("Encounter spec: {encounter_spec}\n").format(encounter_spec=encounter_spec)
        gear_data = _("Gear data pulled from {report_url}\n").format(
            report_url=WCL_URL.format((best_rank.get("report") or {}).get("code", ""))
        )
        log_date = _("Log Date/Time: {datetime} UTC").format(
            datetime=self._time_convert(best_rank.get("startTime") or 0)
        )

        # DMs have no guild (and no guild.me): embeds are always allowed there.
        if ctx.guild is None or ctx.channel.permissions_for(ctx.guild.me).embed_links:
            # embed
            embed = discord.Embed()
            if guild_name:
                title += f"\n{guild_name}"
            embed.title = title[:256]
            embed.description = "\n".join(item_list)[:4096]
            embed.colour = await ctx.embed_color()
            embed.set_footer(text=f"{spec}{ilvl}{gear_data}{log_date}")
            await ctx.send(embed=embed)
        else:
            if guild_name:
                title += f" - {guild_name}"
            msg = "{}\n{}{}{}{}\n\n{}".format(
                title, spec, ilvl, gear_data, log_date, "\n".join(item_list)
            )
            await ctx.send(msg[:2000])

    @gear.autocomplete("realm")
    async def warcraftlogs_gear_realm_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        realms = await self.get_realms(current)
        return realms[:25]

    @commands.bot_has_permissions(embed_links=True)
    @warcraftlogs.command(
        description="Show a character's raid rank overview (best/median performance per encounter).",
        extras={"i18n_desc": {
            "de-DE": "Rang-Übersicht eines Charakters anzeigen (beste/mittlere Leistung pro Encounter).",
            "en-US": "Show a character's raid rank overview (best/median performance per encounter).",
        }},
    )
    @app_commands.choices(
        difficulty=[
            app_commands.Choice(name=difficulty.title(), value=difficulty)
            for difficulty in DIFFICULTIES.values()
        ],
    )
    @app_commands.describe(
        name="Character name",
        realm="Name of the realm",
        zone="The raid zone",
        difficulty="Raid difficulty",
    )
    async def rank(
        self,
        ctx,
        name: str,
        realm: str,
        zone: str = None,
        difficulty: str = None,
    ):
        """
        Character rank overview.

        If the realm name is two words, use a hyphen to connect the words.

        Examples:
        [p]getrank Username Draenor:EU
        [p]getrank Username Alterac-Mountains:US

        Specific Zones (use the autocomplete, the zone name or a short name):
        [p]getrank Username Draenor:EU ToT Heroic
        [p]getrank Username Alterac-Mountains:US "Siege of Orgrimmar" Normal

        Short names (case-insensitive):
        MSV, HoF, ToES, ToT, SoO
        """
        if ctx.interaction:
            # There is no contextual locale for interactions, so we need to set it manually
            # (This is probably a bug in Red, remove this when it's fixed)
            await set_contextual_locales_from_guild(self.bot, ctx.guild)

        # Defer first: everything below may hit the network.
        await ctx.defer()

        if not await self._ensure_client():
            return await ctx.send(self._msg_auth(ctx.clean_prefix))

        # Fetch the live zone list (cached).
        zones = await self._get_zones()
        zones_by_id, zones_by_name = self._zone_maps(zones)

        # someone has their data saved, so they are just trying
        # to look up a zone for themselves
        if name and self._find_zone(name, zones_by_id, zones_by_name) is not None:
            zone = name
            name = None
            realm = None

        # look up any saved info
        userdata = await self.config.user(ctx.author).all()

        region = None
        if realm:
            try:
                realm, region = realm.split(sep=":", maxsplit=1)
            except ValueError:
                # No ":" in the input: treat it as a bare realm name and
                # fall back to the user's saved region below.
                pass

        if not name:
            name = userdata["charname"]
        if not name:
            return await ctx.send(_("Please specify a character name with this command."))
        if not realm:
            realm = userdata["realm"]
        if not realm:
            return await ctx.send(_("Please specify a realm name with this command."))
        if not region:
            region = userdata["region"]
        if not region:
            return await ctx.send(_("Please specify a region like `Realm:EU` with this command."))

        realm = realm.title().replace(" ", "-")
        region = region.upper()

        name = name.title()

        # Resolve zone (autocomplete passes the zone id as value, but accept
        # a zone name or short name too for prefix-command users).
        zone_obj = None
        if zone:
            zone_obj = self._find_zone(zone, zones_by_id, zones_by_name)
            if zone_obj is None:
                return await ctx.send(_("Unknown zone: {zone}").format(zone=zone))
        zone_id = int(zone_obj["id"]) if zone_obj else None
        zone_id_to_name = str(zone_obj.get("name") or zone_id) if zone_obj else None

        # Resolve difficulty (a stable WCL difficulty id or its name).
        if difficulty:
            difficulty_str = str(difficulty)
            if difficulty_str.isdigit() and int(difficulty_str) in DIFFICULTIES:
                difficulty = int(difficulty_str)
            elif difficulty_str.upper() in DIFFICULTIES.values():
                for diff_id, diff_name in DIFFICULTIES.items():
                    if difficulty_str.upper() == diff_name:
                        difficulty = diff_id
                        break
            else:
                difficulty = 0
        else:
            difficulty = 0

        data = None
        if zone_id is None:
            # Probe the newest non-frozen zones first (highest id, capped at
            # ZONE_PROBE_LIMIT) until one has parse data, as no specific zone
            # was requested.
            candidates = self._probe_candidates(zones)
            if not candidates:
                return await ctx.send(_("Could not load the zone list from Warcraft Logs."))
            for zone_candidate in candidates:
                data = await self.http.get_overview(
                    name, realm, region, int(zone_candidate["id"]), difficulty
                )
                err = self._overview_error(data, ctx.clean_prefix, name)
                if err:
                    if data and data.get("error") and not data.get("auth"):
                        # Zone may not support rankings (e.g. dungeons): keep probing.
                        continue
                    return await ctx.send(err)
                if self._zone_rankings(data).get("bestPerformanceAverage") is not None:
                    break
        else:
            # try getting a specific zone's worth of info for this character
            data = await self.http.get_overview(name, realm, region, zone_id, difficulty)
            err = self._overview_error(data, ctx.clean_prefix, name)
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

        result_zone = zones_by_id.get(str(char_data.get("zone"))) or zone_obj
        difficulty = self._difficulty_name(char_data.get("difficulty"), result_zone)
        zone_name = (result_zone or {}).get("name") or str(char_data.get("zone", "?"))
        zone_name = f"⫷ {difficulty} {zone_name} ⫸".center(50, " ")

        embed = discord.Embed()
        embed.title = f"{name.title()} - {realm.title()} ({region.upper()})"
        embed.colour = await ctx.embed_color()

        # perf averages
        embed.add_field(name=zws, value=box(zone_name, lang="fix"), inline=False)

        if not char_data.get("bestPerformanceAverage", None):
            return (
                await ctx.send(
                    _("Nothing found for {zone_name} for this player.").format(
                        zone_name=zone_id_to_name.title()
                    )
                )
                if zone_id
                else await ctx.send(_("Nothing at all found for this player."))
            )
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

        # add rankings per encounter to table (fields read defensively)
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

    @rank.autocomplete("realm")
    async def warcraftlogs_rank_realm_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        realms = await self.get_realms(current)
        return realms[:25]

    @rank.autocomplete("zone")
    async def warcraftlogs_rank_zone_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete the raid zone from the live (cached) WCL zone list.

        Choice value is the WCL zone id (as a string); current zones first,
        newest first. Short names (MSV, HoF, ToES, ToT, SoO) match as well."""
        return self._zone_choices(await self._get_zones(), current)

    @staticmethod
    def humanize_dps(dps: int | float) -> str:
        if dps >= 1000000:
            return f"{round(dps / 1000000)}M"
        elif dps >= 10000:
            return f"{round(dps / 1000)}k"
        else:
            return str(dps)

    @commands.hybrid_group(
        name="wclset-classic",
        description="Commands for setting up WCL settings.",
        extras={"i18n_desc": {
            "de-DE": "Befehle zum Einrichten der WCL-Einstellungen.",
            "en-US": "Commands for setting up WCL settings.",
        }},
    )
    async def wclset(self, ctx: commands.Context):
        """Commands for setting up WCL settings."""
        pass

    @wclset.command(
        name="charname",
        description="Set your character's name.",
        extras={"i18n_desc": {
            "de-DE": "Den Namen deines Charakters festlegen.",
            "en-US": "Set your character's name.",
        }},
    )
    @app_commands.describe(charname="Your character's name")
    async def wclset_charname(self, ctx, charname: str):
        """Set your character's name."""
        await self.config.user(ctx.author).charname.set(charname)
        await ctx.send(
            _("Your character name was set to {charname}.").format(charname=charname.title())
        )

    @wclset.command(
        name="realm",
        description="Set your realm.",
        extras={"i18n_desc": {
            "de-DE": "Deinen Realm festlegen.",
            "en-US": "Set your realm.",
        }},
    )
    @app_commands.describe(realm="Your realm name")
    async def wclset_realm(self, ctx, *, realm: str):
        """Set your realm."""
        realmname = realm.replace(" ", "-")
        await self.config.user(ctx.author).realm.set(realmname)
        await ctx.send(_("Your realm was set to {realm}.").format(realm=realm.title()))

    @wclset.command(
        name="region",
        description="Set your region.",
        extras={"i18n_desc": {
            "de-DE": "Deine Region festlegen.",
            "en-US": "Set your region.",
        }},
    )
    @app_commands.describe(region="Your region (EU, US, KR or TW)")
    async def wclset_region(self, ctx, region: str):
        """Set your region."""
        if region.upper() not in REGIONS:
            return await ctx.send(
                _("Valid regions are: {valid_regions}").format(
                    valid_regions=humanize_list(REGIONS)
                )
            )
        await self.config.user(ctx.author).region.set(region.upper())
        await ctx.send(_("Your realm's region was set to {region}.").format(region=region.upper()))

    @wclset_region.autocomplete("region")
    async def wclset_region_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        current_upper = (current or "").upper()
        return [
            app_commands.Choice(name=r, value=r) for r in REGIONS if current_upper in r
        ][:25]

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
        notification_channel: discord.TextChannel = ctx.guild.get_channel(
            guildinfo["notification_channel"]
        )
        # Guard against an unset or deleted notification channel.
        msg += _("Notification channel: {channel}\n").format(
            channel=notification_channel.name if notification_channel else "None"
        )
        msg += "\n"

        msg += _("[Settings for {user}]\n").format(user=user.display_name)
        charname = userinfo["charname"].title() if userinfo["charname"] else "None"
        realmname = userinfo["realm"].title().replace("-", " ") if userinfo["realm"] else "None"
        regionname = userinfo["region"].upper() if userinfo["region"] else "None"
        msg += _("Character: {charname}\nRealm: {realmname}\nRegion: {regionname}\n\n").format(
            charname=charname, realmname=realmname, regionname=regionname
        )

        msg += _("[Bot Permissions Needed]\n")
        if ctx.message.guild.me.guild_permissions.embed_links:
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
            "Get a key from <https://classic.warcraftlogs.com> by signing up for an account, "
            "then visit your settings.\n"
            "At the bottom is a section called Web API. "
            "Click on the blue link that says `manage your V2 clients here`.\n"
            "Do NOT sign up for a v1 API key, it will not work with this cog.\n"
            "Click on Create Client. Be ready to write down your information somewhere, "
            "you cannot retrieve the secret after this.\n"
            "Enter a name (whatever you want), `https://localhost` for the redirect URL, "
            "and leave the Public Client box unchecked.\n"
            "Use `{prefix}set api warcraftlogs client_id,client-id-goes-here client_secret,"
            "client-secret-goes-here` to set your key.\n "
        ).format(prefix=ctx.prefix)
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
        rarity = item["quality"]
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

    @staticmethod
    def _msg_auth(prefix: str) -> str:
        return _(
            "Warcraft Logs rejected the API credentials or no token could be created. "
            "Check `{prefix}wclset-classic apikey`."
        ).format(prefix=prefix)

    @staticmethod
    def _zone_maps(zones: list) -> Tuple[Dict[str, dict], Dict[str, dict]]:
        """id -> zone and lowercased name -> zone (newest zone wins on equal names)."""
        by_id: Dict[str, dict] = {}
        by_name: Dict[str, dict] = {}
        for z in sorted(zones, key=lambda z: int(z["id"])):
            by_id[str(z["id"])] = z
            by_name[str(z.get("name") or "").lower()] = z
        return by_id, by_name

    @staticmethod
    def _find_zone(
        zone: str, by_id: Dict[str, dict], by_name: Dict[str, dict]
    ) -> Optional[dict]:
        """Resolve a zone id, zone name (case-insensitive) or short name."""
        key = str(zone).strip()
        found = by_id.get(key) or by_name.get(key.lower())
        if found is not None:
            return found
        if key.lower() in ZONE_ALIASES:
            return by_name.get(ZONE_ALIASES[key.lower()].lower())
        return None

    @staticmethod
    def _probe_candidates(zones: list) -> list:
        """Zones probed when no zone was requested: newest first (highest id),
        frozen zones skipped (unless all are frozen), at most ZONE_PROBE_LIMIT."""
        newest = sorted(zones, key=lambda z: int(z["id"]), reverse=True)
        current = [z for z in newest if not z.get("frozen")]
        return (current or newest)[:ZONE_PROBE_LIMIT]

    @staticmethod
    def _zone_choices(zones: list, current: str) -> List[app_commands.Choice[str]]:
        """Zone autocomplete: current (non-frozen) zones, newest first. When the
        user types something, frozen zones are searched as well (after them)."""
        current_lower = (current or "").strip().lower()
        alias = ZONE_ALIASES.get(current_lower, "").lower()
        newest = sorted(zones, key=lambda z: int(z["id"]), reverse=True)
        current_zones = [z for z in newest if not z.get("frozen")]
        if current_lower:
            ordered = current_zones + [z for z in newest if z.get("frozen")]
        else:
            ordered = current_zones or newest
        choices: List[app_commands.Choice[str]] = []
        for z in ordered:
            zname = str(z.get("name") or z["id"])
            if (
                current_lower
                and current_lower not in zname.lower()
                and alias != zname.lower()
            ):
                continue
            choices.append(app_commands.Choice(name=zname[:100], value=str(z["id"])))
            if len(choices) >= 25:
                break
        return choices

    @staticmethod
    def _difficulty_name(diff_id, zone_obj: Optional[dict]) -> str:
        """Difficulty name from the live zone data, then the static table,
        otherwise the raw id (never None)."""
        if diff_id is None:
            return ""
        try:
            diff_id = int(diff_id)
        except (TypeError, ValueError):
            return str(diff_id)
        for d in (zone_obj or {}).get("difficulties") or []:
            try:
                if int(d["id"]) == diff_id and d.get("name"):
                    return str(d["name"])
            except (KeyError, TypeError, ValueError):
                continue
        name = DIFFICULTIES.get(diff_id)
        return name.capitalize() if name else str(diff_id)

    @staticmethod
    def _zone_rankings(data) -> dict:
        try:
            zr = data["data"]["characterData"]["character"]["zoneRankings"]
        except (KeyError, TypeError):
            return {}
        return zr if isinstance(zr, dict) else {}

    def _overview_error(self, data, prefix: str, name: str) -> Optional[str]:
        """Return a user-facing error message for a get_overview result, or None."""
        if data is None:
            return _("The bearer token was invalidated for some reason.")
        if data.get("auth"):
            return self._msg_auth(prefix)
        if data.get("error"):
            return f"WCL API Error: {data['error']}"
        try:
            if not data["data"]["characterData"]["character"]:
                return _("{name} wasn't found on the API.").format(name=name)
        except (KeyError, TypeError):
            return _("Nothing at all found for this player.")
        return None

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

        Only reacts when the shared client_id/client_secret actually change.
        Bearer-only updates of the shared "warcraftlogs" tokens are ignored,
        because this cog keeps its own bearer in its own Config.
        """
        if service_name != "warcraftlogs":
            return
        creds = await self._read_client_creds(api_tokens)
        if creds == self._client_creds:
            return
        self._client_creds = creds
        # New credentials -> request a new bearer for them and swap the session.
        await self._refresh_bearer(force=True)

    @staticmethod
    async def get_realms(current):
        realms = []
        for realm in REALMS.keys():
            if current.lower() not in realm.lower():
                continue
            if len(REALMS[realm]) == 1:
                realms.append(app_commands.Choice(name=realm, value=f"{realm}:{REALMS[realm][0]}"))
            else:
                realms.extend(
                    app_commands.Choice(name=f"{realm} ({region})", value=f"{realm}:{region}")
                    for region in REALMS[realm]
                )
        return realms

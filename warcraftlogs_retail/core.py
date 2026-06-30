# Most of the source of this file for the actual API mechanics can be found at:
# https://github.com/Kowlin/GraphQL-WoWLogs/blob/master/wowlogs/core.py

import json, pprint, io
import logging
import math
import time
from datetime import datetime, timezone
from typing import List, Literal, Mapping, Optional
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

from .autocomplete import REALMS
from .pdc_dashboard import (
    dashboard_widget, dashboard_panel,
    WidgetData, PanelSchema, Field, SubmitResult,
    register_dashboard, unregister_dashboard,
    L, tr, tr_lang,
)
from .enchantid import ENCHANT_ID
from .http import WoWLogsClient, generate_bearer

_ = Translator("WarcraftLogsRetail", __file__)
log = logging.getLogger("red.karlo-cogs.warcraftlogs-retail")

WCL_URL = "https://www.warcraftlogs.com/reports/{}"

# Retail raid difficulties are stable WCL difficulty IDs.
DIFFICULTIES = {
    5: "MYTHIC",
    4: "HEROIC",
    3: "NORMAL",
    1: "LFR",
}

# How long (seconds) to cache the live zone list before re-fetching.
ZONES_CACHE_TTL = 3600


@cog_i18n(_)
class WarcraftLogsRetail(commands.Cog):
    """Retrieve World of Warcraft (Retail) character information from WarcraftLogs."""

    def __init__(self, bot):
        self.bot: Red = bot
        self.config = Config.get_conf(self, identifier=87446677010550785, force_registration=True)
        self.http: WoWLogsClient = None
        self.path = bundled_data_path(self)

        # bearer is stored in this cog's OWN config (NOT shared tokens) so it
        # doesn't clobber the classic cog's bearer (different domain).
        self.config.register_global(bearer_timestamp=0, bearer=None)

        # Live zone cache (retail zone IDs change each patch).
        self._zones_cache: Optional[list] = None
        self._zones_cache_time: float = 0.0

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

    async def _create_client(self) -> None:
        self.http = WoWLogsClient(bearer=await self._get_bearer())
        bearer_status = await self.http.check_bearer()
        if bearer_status is False:
            await generate_bearer(self.bot, self.config)
            await self.http.recreate_session(await self._get_bearer())

    async def _get_bearer(self) -> Optional[str]:
        # Retail bearer lives in this cog's OWN config (not shared tokens).
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

        Retail zone IDs change every patch, so they are fetched from the API
        instead of being hardcoded. The result is cached for ``ZONES_CACHE_TTL``
        seconds."""
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
    @dashboard_panel("wcl_guild", "WarcraftLogs Retail", mount="guild_settings", permission="guild_admin")
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
        region_opts = [
            {"value": "EU", "label": "EU"},
            {"value": "US", "label": "US"},
        ]
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
            v = str(data["region"]).strip()
            await u.region.set(v or None)
        return SubmitResult.ok("Gespeichert.")

    # --- Global panel (bot owner): shared Warcraft Logs API key ---------- #
    # GLOBAL scope (scope="global" + mount="bot_settings") -> appears under the
    # dashboard's global owner-only settings. The SAME panel is registered by
    # the classic cog; both read/write the SHARED "warcraftlogs" tokens, so
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
        self.bot.loop.create_task(self.http.session.close())

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
        name="warcraftlogs-retail",
        aliases=["wcl-retail"],
        description="Retrieve World of Warcraft (Retail) character information from WarcraftLogs.",
        extras={"i18n_desc": {
            "de-DE": "World-of-Warcraft-(Retail)-Charakterinformationen von WarcraftLogs abrufen.",
            "en-US": "Retrieve World of Warcraft (Retail) character information from WarcraftLogs.",
        }},
    )
    async def warcraftlogs(self, ctx: commands.Context):
        """Retrieve World of Warcraft (Retail) character information from WarcraftLogs."""
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

        name = name.title()
        realm, region = realm.split(sep=":")
        realm = ("-".join(realm).title() if isinstance(realm, tuple) else realm.title()).replace(
            " ", "-"
        )
        region = region.upper()

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
        await ctx.defer()

        # Get the user's last raid encounters
        encounters = await self.http.get_last_encounter(name, realm, region)

        if encounters is False:
            # the user wasn't found on the API.
            return await ctx.send(_("{name} wasn't found on the API.").format(name=name))

        error = encounters.get("error", None)
        if error:
            return await ctx.send(f"WCL API Error: {error}")

        if encounters is None:
            return await ctx.send(_("The bearer token was invalidated for some reason."))

        char_data = await self.http.get_gear(name, realm, region, encounters["latest"])
        if not char_data:
            return await ctx.send(
                _("Check your API token and make sure you " "have added it to the bot correctly.")
            )
        gear = None

        if char_data is None:
            # Assuming bearer has been invalidated.
            await self._create_client()

        if len(char_data["encounterRankings"]["ranks"]) != 0:
            # Ensure this is the encounter that has gear listed.
            # IF it's not, we're moving on with the other encounters.
            sorted_by_time = sorted(
                char_data["encounterRankings"]["ranks"],
                key=lambda k: (
                    k["report"]["startTime"] if k["report"]["startTime"] is not None else 0
                ),
                reverse=True,
            )
            gear = sorted_by_time[0]["gear"]
        else:
            encounters["ids"].remove(encounters["latest"])
            for encounter in encounters["ids"]:
                char_data = await self.http.get_gear(name, realm, region, encounter)
                if len(char_data["encounterRankings"]["ranks"]) != 0:
                    sorted_by_time = sorted(
                        char_data["encounterRankings"]["ranks"],
                        key=lambda k: (
                            k["report"]["startTime"] if k["report"]["startTime"] is not None else 0
                        ),
                        reverse=True,
                    )
                    gear = sorted_by_time[0]["gear"]
                    break

        if gear is None:
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
            if item_ilevel_entry and int(item_ilevel_entry) > 5:
                item_ilevel += int(item_ilevel_entry)
                item_count += 1

            # Item-Zeile
            item_list.append(
                f"{slot_name}: {rarity} "
                f"[{item['name']}](https://wowhead.com/item={item['id']}) "
                f"({item_ilevel_entry})"
            )

            # Enchants & Gems
            perm_enchant_id = item.get("permanentEnchant")
            temp_enchant_id = item.get("temporaryEnchant")
            gem_id = item.get("gems")
            gem_id = gem_id[0].get("id") if gem_id else None

            perm_enchant_text = ENCHANT_ID.get(perm_enchant_id)
            temp_enchant_text = ENCHANT_ID.get(temp_enchant_id)
            gem_text = ENCHANT_ID.get(gem_id)

            if perm_enchant_id:
                if temp_enchant_id and temp_enchant_text:
                    symbol = "├"
                elif gem_id and gem_text:
                    symbol = "├"
                else:
                    symbol = "└"
                if perm_enchant_text:
                    item_list.append(f"`{symbol}──` {perm_enchant_text}")
                elif gem_text:
                    item_list.append(f"`{symbol}──` {gem_text}")

            if gem_id and gem_text:
                symbol = "├" if (temp_enchant_id and temp_enchant_text) else "└"
                item_list.append(f"`{symbol}──` {gem_text}")

            if temp_enchant_id and temp_enchant_text:
                item_list.append(f"`└──` {temp_enchant_text}")


        if item_ilevel > 0:
            avg_ilevel = "{:g}".format(item_ilevel / item_count)
        else:
            avg_ilevel = _("Unknown (not present in log data from the API)")

        if ctx.channel.permissions_for(ctx.guild.me).embed_links:
            # embed
            embed = discord.Embed()
            title = f"{name.title()} - {realm.title()} ({region.upper()})"
            guild_name = sorted_by_time[0]["guild"].get("name", None)
            if guild_name:
                title += f"\n{guild_name}"
            embed.title = title
            embed.description = "\n".join(item_list)
            embed.colour = await ctx.embed_color()

            # embed footer
            ilvl = _("Average Item Level: {avg_ilevel}\n").format(avg_ilevel=avg_ilevel)
            encounter_spec = sorted_by_time[0].get("spec", None)
            spec = _("Encounter spec: {encounter_spec}\n").format(encounter_spec=encounter_spec)
            gear_data = _("Gear data pulled from {report_url}\n").format(
                report_url=WCL_URL.format(sorted_by_time[0]["report"]["code"])
            )
            log_date = _("Log Date/Time: {datetime} UTC").format(
                datetime=self._time_convert(sorted_by_time[0]["startTime"])
            )
            embed.set_footer(text=f"{spec}{ilvl}{gear_data}{log_date}")
            await ctx.send(embed=embed)
        else:
            title = f"{name.title()} - {realm.title()} ({region.upper()})"
            guild_name = sorted_by_time[0]["guild"].get("name", None)
            if guild_name:
                title += f" - {guild_name}"
            ilvl = _("Average Item Level: {avg_ilevel}\n").format(avg_ilevel=avg_ilevel)
            encounter_spec = sorted_by_time[0].get("spec", None)
            spec = _("Encounter spec: {encounter_spec}\n").format(encounter_spec=encounter_spec)
            gear_data = _("Gear data pulled from {report_url}\n").format(
                report_url=WCL_URL.format(sorted_by_time[0]["report"]["code"])
            )
            log_date = _("Log Date/Time: {datetime} UTC").format(
                datetime=self._time_convert(sorted_by_time[0]["startTime"])
            )
            msg = "{}\n{}{}{}{}\n\n{}".format(
                title, spec, ilvl, gear_data, log_date, "\n".join(item_list)
            )
            await ctx.send(msg)

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
        [p]getrank Username Area-52:US

        Specific Zones (use the autocomplete to pick a current raid):
        [p]getrank Username Draenor:EU "Nerub-ar Palace" Heroic
        """
        if ctx.interaction:
            # There is no contextual locale for interactions, so we need to set it manually
            # (This is probably a bug in Red, remove this when it's fixed)
            await set_contextual_locales_from_guild(self.bot, ctx.guild)

        # Fetch the live zone list (retail zones change each patch).
        zones = await self._get_zones()
        # Map zone-id (as string) -> zone dict, and lowercased zone name -> id.
        zones_by_id = {str(z["id"]): z for z in zones}
        zones_by_name = {str(z["name"]).lower(): z for z in zones}

        # someone has their data saved, so they are just trying
        # to look up a zone for themselves
        if name and (name in zones_by_id or name.lower() in zones_by_name):
            zone = name
            name = None
            realm = None

        # look up any saved info
        userdata = await self.config.user(ctx.author).all()

        realm, region = realm.split(sep=":")
        realm = ("-".join(realm).title() if isinstance(realm, tuple) else realm.title()).replace(
            " ", "-"
        )
        region = region.upper()

        if not name:
            name = userdata["charname"]
        if not name:
            return await ctx.send(_("Please specify a character name with this command."))
        if not realm:
            realm = userdata["realm"]
        if not realm:
            return await ctx.send(_("Please specify a realm name with this command."))

        await ctx.defer()

        name = name.title()

        # Resolve zone (autocomplete passes the zone id as value, but accept
        # a zone name too for prefix-command users).
        zone_id = None
        zone_id_to_name = None
        if zone:
            zone_obj = zones_by_id.get(str(zone)) or zones_by_name.get(str(zone).lower())
            if zone_obj:
                zone_id = int(zone_obj["id"])
                zone_id_to_name = zone_obj["name"]

        # Resolve difficulty (autocomplete passes a stable WCL difficulty id).
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

        if zone_id is None:
            # return the most recent raid that actually has parse info for this
            # character, as no specific zone was requested. The fetched zone
            # list is ordered newest-first by the API.
            for zone_obj in zones:
                zone_number = int(zone_obj["id"])
                data = await self.http.get_overview(name, realm, region, zone_number, difficulty)
                if error := data.get("error", None):
                    return await ctx.send(f"WCL API Error: {error}")
                if (data is False) or (not data["data"]["characterData"]["character"]):
                    return await ctx.send(_("{name} wasn't found on the API.").format(name=name))
                char_data = data["data"]["characterData"]["character"]["zoneRankings"]
                data_test = char_data.get("bestPerformanceAverage", None)
                if data_test is not None:
                    break
        else:
            # try getting a specific zone's worth of info for this character
            data = await self.http.get_overview(name, realm, region, zone_id, difficulty)
            if error := data.get("error", None):
                return await ctx.send(f"WCL API Error: {error}")
            if (data is False) or (not data["data"]["characterData"]["character"]):
                return await ctx.send(_("{name} wasn't found on the API.").format(name=name))

        # embed and data setup
        zws = "\N{ZERO WIDTH SPACE}"

        try:
            char_data = data["data"]["characterData"]["character"]["zoneRankings"]
        except (KeyError, TypeError):
            msg = _(
                "Something went terribly wrong while trying to "
                "access the zone rankings for this character."
            )
            return await ctx.send(msg)

        try:
            difficulty = (
                await self._difficulty_name_from_id(char_data["difficulty"])
            ).capitalize()
        except KeyError:
            await ctx.send("No data found for that difficulty.")
            return
        zone_name = await self._zone_name_from_id(char_data["zone"])
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
        md_avg = "{:.1f}".format(char_data["medianPerformanceAverage"])
        md_avg = self._get_color(float(md_avg))
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
        rankings = char_data["rankings"]
        for encounter in rankings:
            all_stars = encounter["allStars"]
            enc_details = encounter["encounter"]
            best_amt = (
                self.humanize_dps(encounter["bestAmount"]) if encounter["bestAmount"] != 0 else "-"
            )
            median_pct = (
                "{:.1f}".format(encounter["medianPercent"]) if encounter["medianPercent"] else "-"
            )
            rank_pct = (
                "{:.1f}".format(encounter["rankPercent"]) if encounter["medianPercent"] else "-"
            )
            fastest_kill_tup = self._dynamic_time(encounter["fastestKill"] / 1000)

            if fastest_kill_tup == (0, 0):
                fastest_kill = "-"
            else:
                if len(str(fastest_kill_tup[1])) == 1:
                    seconds = f"0{fastest_kill_tup[1]}"
                else:
                    seconds = fastest_kill_tup[1]
                fastest_kill = f"{fastest_kill_tup[0]}:{seconds}"

            table.rows.append(
                (
                    enc_details.get("name", None),
                    rank_pct,
                    encounter["spec"],
                    best_amt,
                    encounter["totalKills"],
                    fastest_kill,
                    median_pct,
                    all_stars.get("points", None) if all_stars else "-",
                    all_stars.get("rank", None) if all_stars else "-",
                )
            )

        # all stars
        all_stars = char_data["allStars"]
        section_name = _("⫷ Expansion All Stars ⫸").center(50, " ")
        embed.add_field(name=zws, value=box(section_name, lang="Prolog"), inline=False)
        for item in all_stars:
            msg = f"**{item['spec']}**\n"
            rank_percent = "{:.1f}".format(item["rankPercent"])
            msg += _("Points:\n`{points}`\n").format(points=item["points"])
            msg += _("Rank:\n`{rank}`\n").format(rank=item["rank"])
            msg += f"{self._get_color(float(rank_percent), '%')}\n"
            embed.add_field(name=zws, value=msg, inline=True)

        # all stars filler space
        nearest_multiple = (
            len(all_stars) if len(all_stars) % 3 == 0 else 3 * math.ceil(len(all_stars) / 3)
        )
        bonus_empty_fields = nearest_multiple - len(all_stars)
        if bonus_empty_fields > 0:
            for _1 in range(bonus_empty_fields):
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

        Choice value is the zone id (as a string) so the command body can resolve
        it directly, independent of patch-changing zone IDs."""
        zones = await self._get_zones()
        current_lower = (current or "").lower()
        choices: List[app_commands.Choice[str]] = []
        for z in zones:
            name = str(z["name"])
            if current_lower and current_lower not in name.lower():
                continue
            choices.append(app_commands.Choice(name=name[:100], value=str(z["id"])))
            if len(choices) >= 25:
                break
        return choices

    @rank.autocomplete("difficulty")
    async def warcraftlogs_rank_difficulty_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete from the stable retail WCL difficulties.

        Choice value is the WCL difficulty id (as a string)."""
        current_lower = (current or "").lower()
        choices: List[app_commands.Choice[str]] = []
        for diff_id, diff_name in DIFFICULTIES.items():
            label = diff_name.title()
            if current_lower and current_lower not in label.lower():
                continue
            choices.append(app_commands.Choice(name=label, value=str(diff_id)))
        return choices

    @staticmethod
    def humanize_dps(dps: int | float) -> str:
        if dps >= 1000000:
            return f"{round(dps / 1000000)}M"
        elif dps >= 10000:
            return f"{round(dps / 1000)}k"
        else:
            return str(dps)

    @commands.hybrid_group(
        name="wclset-retail",
        description="Commands for setting up WCL (Retail) settings.",
        extras={"i18n_desc": {
            "de-DE": "Befehle zum Einrichten der WCL-(Retail)-Einstellungen.",
            "en-US": "Commands for setting up WCL (Retail) settings.",
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
    @app_commands.describe(region="Your region (EU or US)")
    async def wclset_region(self, ctx, region: str):
        """Set your region."""
        valid_regions = ["EU", "US"]
        if region.upper() not in valid_regions:
            return await ctx.send(
                _("Valid regions are: {valid_regions}").format(
                    valid_regions=humanize_list(valid_regions)
                )
            )
        await self.config.user(ctx.author).region.set(region)
        await ctx.send(_("Your realm's region was set to {region}.").format(region=region.upper()))

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
    @commands.mod_or_permissions(manage_channels=True)
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
        msg += _("Notification channel: {channel}\n").format(channel=notification_channel.name)
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
        return datetime.fromtimestamp(int(time)).strftime("%Y-%m-%d %H:%M:%S")

    async def _zone_name_from_id(self, zoneid: int) -> str:
        # Resolve against the live (cached) zone list instead of a static dict.
        for z in await self._get_zones():
            if int(z["id"]) == int(zoneid):
                return z["name"]
        return str(zoneid)

    @staticmethod
    async def _difficulty_name_from_id(difficultyid: int) -> str:
        for difficulty_id, difficulty_name in DIFFICULTIES.items():
            if difficultyid == difficulty_id:
                return difficulty_name

    def _get_color(self, number: float, bonus=""):
        if number >= 95:
            # legendary
            return self._orange(number, bonus)
        elif 94 >= number > 75:
            # epic
            return self._red(number, bonus)
        elif 75 >= number > 50:
            # rare
            return self._blue(number, bonus)
        elif 50 >= number > 25:
            # common
            return self._green(number, bonus)
        elif 25 >= number >= 0:
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
        """
        if service_name != "warcraftlogs":
            return
        # The shared client_id/client_secret changed -> regenerate this cog's own
        # (retail) bearer and recreate the session with it.
        await self.http.recreate_session(await self._get_token())

    async def _get_token(self, api_tokens: Optional[Mapping[str, str]] = None) -> str:
        """Get the WCL bearer token.

        The bearer is stored in this cog's OWN config (not shared tokens)."""
        bearer = await self.config.bearer()
        if not bearer:
            log.info("No valid token found, trying to create one.")
            await generate_bearer(self.bot, self.config)
            return await self._get_bearer()
        else:
            return bearer

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

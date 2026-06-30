import datetime
import logging
from typing import Any, Dict, Literal, Mapping, Optional

import aiohttp
import discord
from aiolimiter import AsyncLimiter
from aiowowapi import WowApi
from discord.ext import tasks
from raiderio_async import RaiderIO
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n, set_contextual_locales_from_guild
from redbot.core.utils.chat_formatting import humanize_list

from .user_installable.cvardocs import CVar, CVarDocs

from .guildmanage import GuildManage
from .on_message import OnMessage
from .pvp import PvP
from .raiderio import Raiderio
from .scoreboard import Scoreboard
from .token import Token
from .user_installable.raiderio import UserInstallableRaiderio

from .gearcheck import GearCheck
from .charinfo import CharInfo
from .charstats import CharStats
from .talentcheck import TalentCheck
from .raidinfo import RaidInfo
from .comparechars import CompareChars

from .pdc_dashboard import (
    dashboard_widget,
    dashboard_panel,
    dashboard_list,
    WidgetData,
    PanelSchema,
    Field,
    SubmitResult,
    register_dashboard,
    unregister_dashboard,
    L,
    tr,
    tr_lang,
)

log = logging.getLogger("red.karlo-cogs.wowtools")
_ = Translator("WoWTools", __file__)

try:
    from pdc_dashboard.rpc.third_parties import dashboard_page as _dashboard_page  # type: ignore
except Exception:
    try:
        from dashboard.rpc.third_parties import dashboard_page as _dashboard_page  # type: ignore
    except Exception:
        def _dashboard_page(*args: Any, **kwargs: Any):  # type: ignore
            def decorator(func: Any) -> Any:
                func.__dashboard_decorator_params__ = (args, kwargs)
                return func
            return decorator


@cog_i18n(_)
class WoWTools(
    PvP,
    Raiderio,
    Token,
    GuildManage,
    Scoreboard,
    OnMessage,
    CVarDocs,
    GearCheck,
    CharInfo,
    CharStats,
    TalentCheck,
    RaidInfo,
    CompareChars,
    UserInstallableRaiderio,
    commands.Cog,
):
    """Interact with various World of Warcraft APIs"""

    def __init__(self, bot):
        self.on_message_cache: dict = {}
        self.bot: Red = bot
        self.config = Config.get_conf(self, identifier=42069)
        default_global = {
            "wowaudit_key": None,
            "emotes": {
                "gold": None,
                "silver": None,
                "copper": None,
            },
            "assistant_cog_integration": False,
            "status_guild": [],
        }
        default_guild = {
            "language": "en-US",
            "region": None,
            "realm": None,
            "real_guild_name": None,
            "gmanage_guild": None,
            "gmanage_realm": None,
            "guild_rankstrings": {},
            "guild_rankroles": {},
            "guild_log_channel": None,
            "guild_log_welcome_channel": None,
            "guild_roster": {},
            "old_sb": None,
            "scoreboard_channel": None,
            "scoreboard_message": None,
            "scoreboard_blacklist": [],
            "sb_image": False,
            "on_message": False,
            "countdown_channel": None,
            "dashboard_texts": {
                "welcome_note": "Welcome to {guild_name}. Configure your region/realm/guild in WoWTools dashboard.",
                "status_note": "Current setup: {region}/{realm}/{guild_name}",
            },
        }
        default_user = {
            "wow_character_name": None,
            "wow_character_realm": None,
            "wow_character_region": None,
        }
        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
        self.config.register_user(**default_user)
        self.limiter = AsyncLimiter(100, time_period=1)
        self.session = aiohttp.ClientSession(headers={"User-Agent": "Red-DiscordBot/WoWToolsCog"})
        self.raiderio_api = RaiderIO()
        self.blizzard: dict[str, WowApi] = {}
        self.cvar_cache: list[CVar] = []
        self.roster_cache: dict[int, dict] = {}
        self.update_dungeon_scoreboard.start()
        log.info("Dungeon scoreboard updater started.")
        self.guild_log.start()
        log.info("Guild log updater started.")
        self.update_countdown_channels.start()
        log.info("Countdown channel updater started.")
        self.update_bot_status.start()
        log.info("Bot status updater started.")

        self.current_raid = "manaforge-omega"
        self._dashboard_attached = False

        # For countdown channels
        self.early_access_time: dict[str, datetime.datetime] = {}
        self.release_time: dict[str, datetime.datetime] = {}
        # Expansion "early access", or patch release without raid/m+
        self.early_access_time["us"] = datetime.datetime(
            year=2025, month=8, day=15, hour=15, tzinfo=datetime.UTC
        )
        self.early_access_time["eu"] = datetime.datetime(
            year=2025, month=8, day=6, hour=4, tzinfo=datetime.UTC
        )
        # Full expansion release, or season release with raid/m+
        self.release_time["us"] = datetime.datetime(
            year=2025, month=8, day=12, hour=15, tzinfo=datetime.UTC
        )
        self.release_time["eu"] = datetime.datetime(
            year=2025, month=8, day=13, hour=4, tzinfo=datetime.UTC
        )

    async def cog_load(self) -> None:
        raiderio_api_key = await self.bot.get_shared_api_tokens("raiderio")
        self.raiderio_api = RaiderIO(api_key=raiderio_api_key.get("api_key"))
        await self.create_bnet_objs()
        dashboard = self.bot.get_cog("WebDashboard") or self.bot.get_cog("Dashboard")
        if dashboard is not None:
            try:
                dashboard.rpc.third_parties_handler.add_third_party(self, overwrite=True)  # type: ignore[attr-defined]
                self._dashboard_attached = True
            except Exception:
                self._dashboard_attached = False
        register_dashboard(self)

    async def create_bnet_objs(self):
        blizzard_api = await self.bot.get_shared_api_tokens("blizzard")
        cid = blizzard_api.get("client_id")
        secret = blizzard_api.get("client_secret")
        if not cid or not secret:
            return
        self.blizzard["eu"] = WowApi(client_id=cid, client_secret=secret, client_region="eu")
        self.blizzard["us"] = WowApi(client_id=cid, client_secret=secret, client_region="us")
        self.blizzard["kr"] = WowApi(client_id=cid, client_secret=secret, client_region="kr")

    @commands.group(name="wowt-wowset")
    async def wowset(self, ctx):
        """Change WoWTools settings."""
        pass

    @commands.hybrid_group(name="wowt-serverset")
    async def serverset(self, ctx):
        """Change WoW guild-related settings"""
        pass

    @serverset.command(name="region")
    @commands.guild_only()
    @commands.admin()
    async def serverset_region(self, ctx: commands.GuildContext, region: str):
        """Set the region where characters and guilds will be searched for."""
        regions = ("us", "eu", "kr")
        try:
            async with ctx.typing():
                if region not in regions:
                    await ctx.send(
                        _("That region does not exist.\nValid regions are: {regions}.").format(
                            regions=humanize_list(regions),
                        ),
                        ephemeral=True,
                    )
                await self.config.guild(ctx.guild).region.set(region)
            await ctx.send(_("Region set succesfully."), ephemeral=True)
        except Exception as e:
            await ctx.send(_("Command failed successfully. {e}").format(e=e), ephemeral=True)

    @serverset.command(name="realm")
    @commands.guild_only()
    @commands.admin()
    async def serverset_realm(self, ctx: commands.GuildContext, realm: str | None = None):
        """Set the realm of your guild."""
        try:
            async with ctx.typing():
                if not realm:
                    await self.config.guild(ctx.guild).realm.clear()
                    await ctx.send(_("Realm cleared."), ephemeral=True)
                    return
                realm = realm.lower()
                await self.config.guild(ctx.guild).realm.set(realm)
            await ctx.send(_("Realm set."), ephemeral=True)
        except Exception as e:
            await ctx.send(_("Command failed successfully. {e}").format(e=e), ephemeral=True)

    @serverset.command(name="guild")
    @commands.guild_only()
    @commands.admin()
    async def serverset_guild(self, ctx: commands.GuildContext, guild_name: str | None = None):
        """Set the name of your guild."""
        try:
            async with ctx.typing():
                if guild_name is None:
                    await self.config.guild(ctx.guild).real_guild_name.clear()
                    await ctx.send(_("Guild name cleared."), ephemeral=True)
                    return
                guild_name = guild_name.replace("-", " ").title()
                await self.config.guild(ctx.guild).real_guild_name.set(guild_name)
            await ctx.send(_("Guild name set."), ephemeral=True)
        except Exception as e:
            await ctx.send(_("Command failed successfully. {e}").format(e=e), ephemeral=True)

    @wowset.command(name="blizzard")
    @commands.is_owner()
    async def wowset_blizzard(self, ctx: commands.Context):
        """Instructions for setting up the Blizzard API."""
        await ctx.send(
            _(
                "Create a client on https://develop.battle.net/ and then type in "
                "`{prefix}set api blizzard client_id,whoops client_secret,whoops` "
                "filling in `whoops` with your client's ID and secret."
            ).format(prefix=ctx.prefix)
        )
        return

    @wowset.command(name="emote")
    @commands.is_owner()
    async def wowset_emote(
        self, ctx: commands.Context, currency: str, emoji: discord.Emoji = None
    ):
        """Set the emotes used for gold, silver and copper."""
        currency = currency.lower()
        if currency not in ["gold", "silver", "copper"]:
            return await ctx.send(_("Invalid currency."))
        if emoji:
            await self.config.emotes.set_raw(currency, value=str(emoji))
            await ctx.send(
                _("{currency} emote set to {emoji}").format(currency=currency.title(), emoji=emoji)
            )
        else:
            await self.config.emotes.set_raw(currency, value=None)
            await ctx.send(_("{currency} emote removed.").format(currency=currency.title()))

    @serverset.command(name="images")
    @commands.admin()
    @commands.guild_only()
    async def serverset_images(self, ctx: commands.Context):
        """Toggle scoreboard images on or off."""
        enabled = await self.config.guild(ctx.guild).sb_image()
        if enabled:
            await self.config.guild(ctx.guild).sb_image.set(False)
            await ctx.send(_("Images disabled."), ephemeral=True)
        else:
            await self.config.guild(ctx.guild).sb_image.set(True)
            await ctx.send(_("Images enabled."), ephemeral=True)

    @wowset.group(name="character")
    async def wowset_character(self, ctx):
        """Character settings."""
        pass

    @wowset_character.command(name="name")
    async def wowset_character_name(self, ctx, character_name: str):
        """Set your character name."""
        await self.config.user(ctx.author).wow_character_name.set(character_name)
        await ctx.send(_("Character name set."))

    @wowset_character.command(name="realm")
    async def wowset_character_realm(self, ctx, realm_name: str):
        """Set your character's realm."""
        await self.config.user(ctx.author).wow_character_realm.set(realm_name)
        await ctx.send(_("Character realm set."))

    @wowset_character.command(name="region")
    async def wowset_character_region(self, ctx, region: str):
        """Set your character's region."""
        regions = ("us", "eu", "kr")
        if region.lower() not in regions:
            await ctx.send(
                _("That region does not exist.\nValid regions are: {regions}.").format(
                    regions=", ".join(regions)
                )
            )
            return
        await self.config.user(ctx.author).wow_character_region.set(region)
        await ctx.send(_("Character region set."))

    @serverset.command(
        name="onmessage",
        description="Toggle the bot's ability to respond to messages when a supported spell/item name is mentioned.",
    )
    @commands.guild_only()
    @checks.mod_or_permissions(manage_guild=True)
    async def serverset_on_message(self, ctx: commands.Context):
        """Toggle the bot's ability to respond to messages when a supported spell/item name is mentioned.

        Example: `I think [[Ebon Might]] is cool.`"""
        enabled = await self.config.guild(ctx.guild).on_message()
        if enabled:
            await self.config.guild(ctx.guild).on_message.set(False)
            await ctx.send(_("On message disabled."))
        else:
            await self.config.guild(ctx.guild).on_message.set(True)
            await ctx.send(_("On message enabled."))

    @wowset.command(name="assintegration")
    @commands.is_owner()
    async def wowset_assintegration(self, ctx: commands.Context):
        """Toggle the assistant cog integration."""
        enabled = await self.config.assistant_cog_integration()
        if enabled:
            await self.config.assistant_cog_integration.set(False)
            await ctx.send(_("Assistant cog integration disabled."))
        else:
            await self.config.assistant_cog_integration.set(True)
            await ctx.send(_("Assistant cog integration enabled."))

    @serverset.command(name="patchcountdown")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_guild=True, manage_channels=True)
    async def serverset_patchcountdown(self, ctx: commands.Context):
        "Add or remove a locked channel that will display the time until the next patch releases."
        cd_channel_id = await self.config.guild(ctx.guild).countdown_channel()
        region = await self.config.guild(ctx.guild).region()  # type: ignore
        if cd_channel_id:
            cd_channel = ctx.guild.get_channel(cd_channel_id)
            if cd_channel:
                await cd_channel.delete(
                    reason=_(
                        "User with ID {cmd_author} requested deletion of countdown channel."
                    ).format(cmd_author=ctx.author.id)
                )
            await self.config.guild(ctx.guild).countdown_channel.clear()
            await ctx.send(_("Countdown channel removed"))
            return

        now = datetime.datetime.now(datetime.UTC)
        early_access_time = self.early_access_time.get(region)
        release_time = self.release_time.get(region)
        if not early_access_time or not release_time:
            await ctx.send(_("Not available for the {region} region.").format(region=region))
            return

        diff = early_access_time - now
        early_access = True
        if diff.total_seconds() < 0:
            diff = release_time - now
            early_access = False
        if diff.total_seconds() < 0:
            await ctx.send(_("New season has already released."))
            return

        days = diff.days
        hours, remainder = divmod(diff.seconds, 3600)
        minutes, __ = divmod(remainder, 60)
        if diff.days > 0:
            time_str = f"{days}d{hours}h{minutes}m"
        else:
            time_str = f"{hours}h {minutes}m"

        channel_name = (
            _("🔴Patch in {countdown}").format(countdown=time_str)
            if early_access
            else _("🟡Season in {countdown}").format(countdown=time_str)
        )
        perms = {
            ctx.guild.default_role: discord.PermissionOverwrite(connect=False),
        }

        channel = await ctx.guild.create_voice_channel(
            channel_name, position=0, category=None, overwrites=perms
        )
        await self.config.guild(ctx.guild).countdown_channel.set(channel.id)
        await ctx.tick()

    @tasks.loop(minutes=6)
    async def update_countdown_channels(self):
        for guild in self.bot.guilds:
            if await self.bot.cog_disabled_in_guild(self, guild):
                continue
            region = await self.config.guild(guild).region()
            countdown_channel_id: int = await self.config.guild(guild).countdown_channel()
            if countdown_channel_id is None:
                continue
            await set_contextual_locales_from_guild(self.bot, guild)

            countdown_channel = guild.get_channel(countdown_channel_id)
            if not countdown_channel:
                continue

            now = datetime.datetime.now(datetime.UTC)
            early_access_time = self.early_access_time.get(region)
            release_time = self.release_time.get(region)
            if not early_access_time or not release_time:
                log.debug("Early access or release time not set for region {}".format(region))
                continue

            diff = early_access_time - now
            early_access = True
            if diff.total_seconds() < 0:
                diff = release_time - now
                early_access = False
            if diff.total_seconds() < 0:
                await countdown_channel.delete()
                await self.config.guild(guild).countdown_channel.clear()
                return

            days = diff.days
            hours, remainder = divmod(diff.seconds, 3600)
            minutes, __ = divmod(remainder, 60)
            if diff.days > 0:
                time_str = f"{days}d{hours}h{minutes}m"
            else:
                time_str = f"{hours}h {minutes}m"

            channel_name = (
                _("🔴Patch in {countdown}").format(countdown=time_str)
                if early_access
                else _("🟡Season in {countdown}").format(countdown=time_str)
            )
            try:
                await countdown_channel.edit(name=channel_name)
            except Exception as e:
                # Probably rate limit stuff. Just ignore.
                log.debug("Exception in countdown channel editing. {}".format(e))

    @wowset.command(name="status", hidden=True)
    async def wowset_status(
        self,
        ctx: commands.Context,
        guild_name: str,
        realm: str,
        region: str,
        emoji: Optional[discord.Emoji] = None,
    ):
        """Set the guild whose raid progression is shown as the bot's status."""
        status_guild = [
            guild_name.replace("-", " ").lower(),
            realm,
            region,
            emoji.id if emoji else None,
        ]
        await self.config.status_guild.set(status_guild)
        if await self.set_bot_status():
            await ctx.send(_("Status guild set."))
            return
        await ctx.send(_("Setting guild bot status failed."))

    async def set_bot_status(self) -> bool:
        try:
            guild, realm, region, emoji = await self.config.status_guild()
        except ValueError:
            return False

        guild_data = await self.raiderio_api.get_guild_profile(
            region,
            realm,
            guild,
            fields=["raid_progression"],
        )
        try:
            guild: str = guild_data["name"]
            progress: str = guild_data["raid_progression"][self.current_raid]["summary"]
        except KeyError:
            return False
        activity = discord.CustomActivity(name=f"{guild}: {progress}", emoji=emoji)
        await self.bot.change_presence(activity=activity)
        return True

    @tasks.loop(minutes=60)
    async def update_bot_status(self):
        if not await self.set_bot_status():
            log.debug("Setting the bot's status failed.")

    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name: str, api_tokens: Mapping[str, str]):
        """
        Lifted shamelessly from GHC.
        Thanks Kowlin for this
        """
        if service_name != "blizzard":
            return
        await self.create_bnet_objs()

    @dashboard_widget("wowtools_onmessage", "WoW Auto-Reply", size="sm", permission="guild_member")
    async def wowtools_onmessage_widget(self, ctx):
        try:
            enabled = await self.config.guild(ctx.guild).on_message()
            return WidgetData.kpi(value="An" if enabled else "Aus", label="WoW Auto-Reply")
        except Exception:
            return WidgetData.kpi(value="–", label="WoW Auto-Reply")

    # --- Guild panel: auto-reply & channels ------------------------------ #
    @dashboard_panel("settings", L("WoWTools (Server)", "WoWTools (Server)"), mount="guild_settings", permission="guild_admin")
    async def wowtools_guild_panel(self, ctx):
        g = self.config.guild(ctx.guild)
        ch_opts = [{"value": "", "label": "— no channel —"}] + [
            {"value": str(c.id), "label": "#" + c.name} for c in ctx.guild.text_channels
        ]
        region_opts = [
            {"value": "eu", "label": "eu"},
            {"value": "us", "label": "us"},
            {"value": "kr", "label": "kr"},
        ]
        dt = await g.dashboard_texts()
        if not isinstance(dt, dict):
            dt = {}
        text_vars = [
            {"token": "{guild_name}", "desc": "Guild name"},
            {"token": "{region}", "desc": "Region"},
            {"token": "{realm}", "desc": "Realm"},
        ]
        return PanelSchema(
            fields=[
                Field.switch("on_message", "Auto-reply to WoW terms", value=bool(await g.on_message())),
                Field.select("region", "Region", region_opts, value=str(await g.region() or "")),
                Field.text("realm", "Realm", value=str(await g.realm() or "")),
                Field.text("real_guild_name", "Guild name (actual)", value=str(await g.real_guild_name() or "")),
                Field.select("guild_log_channel", "Log channel", ch_opts, value=str(await g.guild_log_channel() or "")),
                Field.select("scoreboard_channel", "Scoreboard channel", ch_opts, value=str(await g.scoreboard_channel() or "")),
                Field.select("countdown_channel", "Countdown channel", ch_opts, value=str(await g.countdown_channel() or "")),
                Field.textarea("welcome_note", "Welcome text", value=str(dt.get("welcome_note", "")), variables=text_vars),
                Field.textarea("status_note", "Status text", value=str(dt.get("status_note", "")), variables=text_vars),
            ]
        )

    @wowtools_guild_panel.on_submit
    async def _save_wowtools_guild(self, ctx, data):
        g = self.config.guild(ctx.guild)
        if "on_message" in data:
            await g.on_message.set(bool(data["on_message"]))
        if "region" in data:
            v = str(data["region"]).lower().strip()
            await g.region.set(v if v in ("eu", "us", "kr") else None)
        if "realm" in data:
            v = str(data["realm"]).strip()
            await g.realm.set(v or None)
        if "real_guild_name" in data:
            v = str(data["real_guild_name"]).strip()
            await g.real_guild_name.set(v or None)
        if "welcome_note" in data or "status_note" in data:
            dt = await g.dashboard_texts()
            if not isinstance(dt, dict):
                dt = {}
            if "welcome_note" in data:
                dt["welcome_note"] = str(data["welcome_note"])
            if "status_note" in data:
                dt["status_note"] = str(data["status_note"])
            await g.dashboard_texts.set(dt)
        if "guild_log_channel" in data:
            v = data["guild_log_channel"]
            await g.guild_log_channel.set(int(v) if v else None)
        if "scoreboard_channel" in data:
            v = data["scoreboard_channel"]
            await g.scoreboard_channel.set(int(v) if v else None)
        if "countdown_channel" in data:
            v = data["countdown_channel"]
            await g.countdown_channel.set(int(v) if v else None)
        return SubmitResult.ok(tr(ctx, "Gespeichert.", "Saved."))

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

    # --- Global panel (bot owner): API tokens ---------------------------- #
    @dashboard_panel("api_tokens", L("WoW API-Tokens", "WoW API Tokens"), scope="global", mount="bot_settings", permission="bot_owner")
    async def wowtools_api_panel(self, ctx):
        bliz = await self.bot.get_shared_api_tokens("blizzard")
        rio = await self.bot.get_shared_api_tokens("raiderio")
        return PanelSchema(
            description=tr(ctx, "Geteilte API-Tokens (Blizzard Battle.net, Raider.IO).", "Shared API tokens (Blizzard Battle.net, Raider.IO)."),
            fields=[
                Field.text("blizzard_client_id", "Blizzard Client ID", value=bliz.get("client_id", "")),
                Field.text("blizzard_client_secret", "Blizzard Client Secret", value=bliz.get("client_secret", "")),
                Field.text("raiderio_api_key", "Raider.IO API Key", value=rio.get("api_key", "")),
            ],
        )

    @wowtools_api_panel.on_submit
    async def _save_wowtools_api(self, ctx, data):
        await self.bot.set_shared_api_tokens(
            "blizzard",
            client_id=str(data.get("blizzard_client_id", "")).strip(),
            client_secret=str(data.get("blizzard_client_secret", "")).strip(),
        )
        await self.bot.set_shared_api_tokens(
            "raiderio", api_key=str(data.get("raiderio_api_key", "")).strip()
        )
        try:
            await self.create_bnet_objs()
            self.raiderio_api = RaiderIO(api_key=str(data.get("raiderio_api_key", "")).strip())
        except Exception:
            pass
        return SubmitResult.ok(tr(ctx, "API-Tokens gespeichert.", "API tokens saved."))

    # --- Guild list: scoreboard blacklist (character names) -------------- #
    @dashboard_list(
        "scoreboard_blacklist", L("Scoreboard-Blacklist", "Scoreboard Blacklist"), mount="guild_settings",
        permission="guild_admin",
        columns=[{"key": "name", "label": "Character"}],
        description=L("Vom Scoreboard ausgeschlossene Charaktere. Hinzufügen per Befehl.", "Characters excluded from the scoreboard. Add via command."),
    )
    async def wowtools_blacklist_list(self, ctx):
        names = await self.config.guild(ctx.guild).scoreboard_blacklist()
        return [
            {"id": str(n), "cells": {"name": str(n)}}
            for n in (names or [])
        ]

    @wowtools_blacklist_list.on_delete
    async def _wowtools_blacklist_delete(self, ctx, item_id):
        async with self.config.guild(ctx.guild).scoreboard_blacklist() as bl:
            # remove by value (list of names)
            matches = [n for n in bl if str(n) == str(item_id)]
            if not matches:
                return SubmitResult.fail(tr(ctx, "Eintrag nicht gefunden.", "Entry not found."))
            for n in matches:
                bl.remove(n)
        return SubmitResult.ok(tr(ctx, "Von der Blacklist entfernt.", "Removed from the blacklist."))

    async def cog_unload(self):
        unregister_dashboard(self)
        self.bot.loop.create_task(self.session.close())
        self.update_dungeon_scoreboard.cancel()
        self.guild_log.cancel()
        self.update_countdown_channels.cancel()
        self.update_bot_status.cancel()
        log.info("All tasks cancelled.")

    @commands.Cog.listener()
    async def on_dashboard_cog_add(self, dashboard_cog: commands.Cog) -> None:
        if self._dashboard_attached:
            return
        try:
            dashboard_cog.rpc.third_parties_handler.add_third_party(self, overwrite=True)  # type: ignore[attr-defined]
            self._dashboard_attached = True
        except Exception:
            self._dashboard_attached = False

    @commands.Cog.listener()
    async def on_cog_add(self, cog: commands.Cog) -> None:
        # Compatibility path for Dashboard variants that do not dispatch `dashboard_cog_add`.
        if self._dashboard_attached:
            return
        if cog.qualified_name not in {"Dashboard", "WebDashboard"}:
            return
        try:
            cog.rpc.third_parties_handler.add_third_party(self, overwrite=True)  # type: ignore[attr-defined]
            self._dashboard_attached = True
        except Exception:
            self._dashboard_attached = False

    @_dashboard_page(name=None, description="WoWTools Dashboard")
    async def dashboard_home(self, **kwargs: Any) -> Dict[str, Any]:
        _ = kwargs
        source = """
<div style="padding: 12px;">
  <h2>WoWTools</h2>
  <p>Dashboard integration is active.</p>
  <p>Use the page <b>wowtools</b> for guild-specific settings.</p>
</div>
"""
        return {
            "status": 0,
            "web_content": {
                "source": source,
                "standalone": True,
            },
        }

    @_dashboard_page(
        name="wowtools",
        description="Guild-side WoWTools settings and text defaults.",
        methods=("GET", "POST"),
        context_ids=["user_id", "guild_id"],
        hidden=False,
    )
    async def dashboard_wowtools(
        self,
        user_id: Optional[int] = None,
        guild_id: Optional[int] = None,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if user_id is None or guild_id is None:
            return {"status": 0, "error_code": 400, "message": "Missing context user_id/guild_id."}
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return {"status": 1, "message": "Guild not found."}
        member = guild.get_member(user_id)
        if member is None or not member.guild_permissions.manage_guild:
            if user_id not in self.bot.owner_ids:
                return {"status": 1, "message": "Not allowed."}

        gconf = self.config.guild(guild)
        region = await gconf.region()
        realm = await gconf.realm()
        gname = await gconf.real_guild_name()
        on_message = await gconf.on_message()
        texts = await gconf.dashboard_texts()

        if method.upper() == "POST" and data:
            form = dict(data.get("form", {}))
            new_region = str(form.get("region", region or "")).lower().strip()
            if new_region in ("eu", "us", "kr"):
                await gconf.region.set(new_region)
            await gconf.realm.set(str(form.get("realm", realm or "")).strip() or None)
            await gconf.real_guild_name.set(str(form.get("guild_name", gname or "")).strip() or None)
            await gconf.on_message.set(str(form.get("on_message", "off")).lower() in ("on", "true", "1", "yes"))
            texts["welcome_note"] = str(form.get("welcome_note", texts.get("welcome_note", "")))
            texts["status_note"] = str(form.get("status_note", texts.get("status_note", "")))
            await gconf.dashboard_texts.set(texts)
            return {
                "status": 0,
                "notifications": [{"message": "WoWTools dashboard settings saved.", "category": "success"}],
                "redirect_url": kwargs.get("request_url"),
            }

        source = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
* {{ font-family: 'Inter', sans-serif; box-sizing: border-box; }}
.pdc-dashboard .card {{ background: rgba(18, 23, 33, 0.6); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3); border-radius: 12px; padding: 24px; color: #e8eefc; transition: all 0.3s ease; }}
.pdc-dashboard .card:hover {{ box-shadow: 0 12px 40px 0 rgba(0, 0, 0, 0.4); border-color: rgba(255, 255, 255, 0.12); }}
.pdc-dashboard h2, .pdc-dashboard h3 {{ color: #ffffff; font-weight: 600; margin-top: 0; margin-bottom: 16px; letter-spacing: -0.02em; }}
.pdc-dashboard p {{ color: #a0aec0; font-size: 14px; line-height: 1.5; margin-top: 0; margin-bottom: 16px; }}
.pdc-dashboard code {{ background: rgba(255, 255, 255, 0.1); padding: 4px 8px; border-radius: 6px; font-size: 13px; color: #63b3ed; font-family: monospace; }}
.pdc-dashboard label {{ font-size: 13.5px; font-weight: 500; color: #cbd5e0; margin-bottom: 8px; display: inline-block; }}
.pdc-dashboard input, .pdc-dashboard textarea, .pdc-dashboard select {{ width: 100%; padding: 12px 16px; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.1); background: rgba(0, 0, 0, 0.25); color: #fff; font-size: 14px; transition: all 0.2s ease; margin-bottom: 16px; }}
.pdc-dashboard input:focus, .pdc-dashboard textarea:focus, .pdc-dashboard select:focus {{ outline: none; border-color: #4299e1; box-shadow: 0 0 0 3px rgba(66, 153, 225, 0.25); background: rgba(0, 0, 0, 0.35); }}
.pdc-dashboard button {{ padding: 12px 24px; border-radius: 8px; border: none; background: linear-gradient(135deg, #4299e1 0%, #3182ce 100%); color: #fff; font-weight: 600; cursor: pointer; transition: all 0.2s ease; box-shadow: 0 4px 6px rgba(50, 50, 93, 0.11), 0 1px 3px rgba(0, 0, 0, 0.08); font-size: 14px; }}
.pdc-dashboard button:hover {{ transform: translateY(-1px); box-shadow: 0 7px 14px rgba(50, 50, 93, 0.15), 0 3px 6px rgba(0, 0, 0, 0.1); background: linear-gradient(135deg, #3182ce 0%, #2b6cb0 100%); }}
.pdc-dashboard button:active {{ transform: translateY(1px); }}
</style>
<div class='pdc-dashboard'>
<div class='card'>
<h2>WoWTools - Guild Dashboard</h2>
<p><b>Variables:</b> <code>{{guild_name}}</code> <code>{{region}}</code> <code>{{realm}}</code></p>
<form method='post'>
<label>Region</label><select name='region'>
<option value='eu' {'selected' if (region or 'eu') == 'eu' else ''}>eu</option>
<option value='us' {'selected' if region == 'us' else ''}>us</option>
<option value='kr' {'selected' if region == 'kr' else ''}>kr</option>
</select><br><br>
<label>Realm</label><input name='realm' value='{(realm or '').replace("'", "&#39;")}'><br><br>
<label>Guild Name</label><input name='guild_name' value='{(gname or '').replace("'", "&#39;")}'><br><br>
<label><input type='checkbox' name='on_message' {'checked' if on_message else ''}> Enable on_message feature</label><br><br>
<label>Welcome note template</label><textarea rows='2' name='welcome_note'>{texts.get('welcome_note','')}</textarea><br><br>
<label>Status note template</label><textarea rows='2' name='status_note'>{texts.get('status_note','')}</textarea><br><br>
<button type='submit'>Save</button>
</form>
</div>
</div>
"""
        return {"status": 0, "web_content": {"source": source, "standalone": True}}

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ):
        await self.config.user_from_id(user_id).clear()

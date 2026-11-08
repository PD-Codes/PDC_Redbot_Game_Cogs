import html
import logging
from typing import Any, Dict, Literal, Mapping, Optional

import aiohttp
import discord
from aiolimiter import AsyncLimiter
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils.chat_formatting import humanize_list

from .user_installable.cvardocs import CVar, CVarDocs

from .groups import WoWTFGroups, send_group_help, wowtfset
from .guildmanage import GuildManage
from .legacy import LegacyRedirects
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
from .forever_api import get_lang, not_available_msg, reset_token_cache

from .pdc_dashboard import (
    dashboard_widget,
    dashboard_panel,
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

log = logging.getLogger("red.karlo-cogs.wowtools_forever")
_ = Translator("WoWToolsForever", __file__)

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
class WoWToolsForever(
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
    LegacyRedirects,
    WoWTFGroups,
    commands.Cog,
):
    """Interact with the World of Warcraft: Forever APIs (characters are name + surname, no realms)."""

    def __init__(self, bot):
        self.on_message_cache: dict = {}
        self.bot: Red = bot
        # Own config namespace (class name WoWToolsForever + own identifier) -> retail,
        # classic and forever keep SEPARATE settings when loaded side by side.
        self.config = Config.get_conf(self, identifier=420691104)
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
            # Legacy key, only read as a fallback for old data. The in-game guild name
            # is stored in "gmanage_guild" (see GuildManage.get/set_wow_guild_name).
            "real_guild_name": None,
            "gmanage_guild": None,
            "guild_rankstrings": {},
            "guild_rankroles": {},
            "guild_log_channel": None,
            "guild_log_welcome_channel": None,
            "guild_roster": {},
            # Guild slug the stored guild_roster belongs to (detects a guild change).
            "guild_roster_guild": None,
            "on_message": False,
            # Unused in WoW Forever (no patch countdown). Kept registered so stored
            # values stay untouched; nothing reads, deletes or clears them.
            "countdown_channel": None,
            "dashboard_texts": {
                "welcome_note": "Welcome to {guild_name}. Configure your region/guild in the WoWTools Forever dashboard.",
                "status_note": "Current setup: {region}/{guild_name}",
            },
        }
        default_user = {
            "wow_character_name": None,
            "wow_character_surname": None,
            "wow_character_region": None,
        }
        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
        self.config.register_user(**default_user)
        self.limiter = AsyncLimiter(100, time_period=1)
        self.session = aiohttp.ClientSession(headers={"User-Agent": "Red-DiscordBot/WoWToolsForeverCog"})
        self.cvar_cache: list[CVar] = []
        self.roster_cache: dict[int, dict] = {}
        # No Mythic+ scoreboard and no Raider.IO bot status in WoW Forever -> those
        # background loops of the Classic cog are not started here.
        self.guild_log.start()
        log.info("Guild log updater started.")
        # The patch countdown channel loop of the Retail/Classic cogs is intentionally
        # NOT started: WoW Forever has no patch dates, and with past dates the loop
        # would delete whatever channel is configured.

        self._dashboard_attached = False

    async def cog_load(self) -> None:
        await self.create_bnet_objs()
        dashboard = self.bot.get_cog("pdc_webdashboard") or self.bot.get_cog("WebDashboard") or self.bot.get_cog("Dashboard")
        if dashboard is not None:
            try:
                dashboard.rpc.third_parties_handler.add_third_party(self, overwrite=True)  # type: ignore[attr-defined]
                self._dashboard_attached = True
            except Exception:
                self._dashboard_attached = False
        register_dashboard(self)

    async def create_bnet_objs(self):
        # All Blizzard calls go through forever_api (client credentials, token cached per
        # region). Changing the credentials only needs the cached tokens to be dropped.
        reset_token_cache(self)

    # Bot-owner settings: prefix-only, like the former wowtf-wowset group.
    @wowtfset.group(name="bot", with_app_command=False)
    async def wowset(self, ctx):
        """Bot owner settings for WoWTools Forever."""
        await send_group_help(ctx)

    @wowtfset.group(name="server")
    async def serverset(self, ctx):
        """Change WoW guild-related settings"""
        pass

    @serverset.command(name="region")
    @commands.guild_only()
    @commands.admin()
    async def serverset_region(self, ctx: commands.GuildContext, region: str):
        """Set the region where characters and guilds will be searched for."""
        regions = ("us", "eu", "kr")
        region = (region or "").lower()
        try:
            async with ctx.typing():
                if region not in regions:
                    await ctx.send(
                        _("That region does not exist.\nValid regions are: {regions}.").format(
                            regions=humanize_list(regions),
                        ),
                        ephemeral=True,
                    )
                    return
                await self.config.guild(ctx.guild).region.set(region)
            await ctx.send(_("Region set succesfully."), ephemeral=True)
        except Exception as e:
            await ctx.send(_("Command failed successfully. {e}").format(e=e), ephemeral=True)

    @serverset.command(name="guild")
    @commands.guild_only()
    @commands.admin()
    async def serverset_guild(self, ctx: commands.GuildContext, *, guild_name: Optional[str] = None):
        """Set the name of your guild (empty = clear)."""
        try:
            async with ctx.typing():
                if not (guild_name or "").strip():
                    await self.set_wow_guild_name(ctx.guild, None)
                    await ctx.send(_("Guild name cleared."), ephemeral=True)
                    return
                await self.set_wow_guild_name(ctx.guild, guild_name)
            await ctx.send(_("Guild name set."), ephemeral=True)
        except Exception as e:
            await ctx.send(_("Command failed successfully. {e}").format(e=e), ephemeral=True)

    @wowset.command(name="blizzard", with_app_command=False)
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

    @wowset.command(name="emote", with_app_command=False)
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
        """Toggle scoreboard images on or off (not available in WoW Forever)."""
        lang = await get_lang(self, ctx)
        await ctx.send(
            not_available_msg(lang, "Mythic+-Scoreboard", "Mythic+ scoreboard"), ephemeral=True
        )

    # Former "wowtf-wowset character ..." (prefix-only); now also a slash subgroup
    # so every user can save the character used as default by /wowtf commands.
    @wowtfset.group(name="me")
    async def wowset_character(self, ctx):
        """Your saved WoW Forever character (default for /wowtf commands)."""
        await send_group_help(ctx)

    @wowset_character.command(name="name")
    async def wowset_character_name(self, ctx, character_name: str):
        """Set your character name."""
        await self.config.user(ctx.author).wow_character_name.set(character_name)
        await ctx.send(_("Character name set."))

    @wowset_character.command(name="surname")
    async def wowset_character_surname(self, ctx, *, surname: str):
        """Set your character's surname (WoW Forever has no realms)."""
        await self.config.user(ctx.author).wow_character_surname.set(surname.strip())
        lang = await get_lang(self, ctx)
        await ctx.send(tr_lang(lang, "Nachname des Charakters gesetzt.", "Character surname set."))

    @wowset_character.command(name="region")
    async def wowset_character_region(self, ctx, region: str):
        """Set your character's region."""
        regions = ("us", "eu", "kr")
        region = region.lower()
        if region not in regions:
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
            msg = _("On message enabled.")
            hint = await self.onmessage_conflict_hint(ctx.guild, await get_lang(self, ctx))
            await ctx.send(f"{msg}\n{hint}" if hint else msg)

    @wowset.command(name="assintegration", with_app_command=False)
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
        """Patch countdown channel (not available in WoW Forever)."""
        # Intentionally a no-op: never creates, edits or deletes channels and never
        # touches a stored countdown_channel value.
        lang = await get_lang(self, ctx)
        await ctx.send(
            not_available_msg(lang, "Patch-Countdown-Kanal", "Patch countdown channel"), ephemeral=True
        )

    @wowset.command(name="status", hidden=True, with_app_command=False)
    @commands.is_owner()
    async def wowset_status(self, ctx: commands.Context, *, args: Optional[str] = None):
        """Set the guild whose raid progression is shown as the bot's status.

        Not available in WoW Forever (the Classic/Retail version uses Raider.IO).
        """
        lang = await get_lang(self, ctx)
        await ctx.send(
            not_available_msg(lang, "Bot-Status (Raider.IO-Raidfortschritt)", "Bot status (Raider.IO raid progression)")
        )

    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name: str, api_tokens: Mapping[str, str]):
        """
        Lifted shamelessly from GHC.
        Thanks Kowlin for this
        """
        if service_name != "blizzard":
            return
        await self.create_bnet_objs()

    @dashboard_widget("wowtoolsforever_onmessage", "WoW Forever Auto-Reply", size="sm", permission="guild_member")
    async def wowtools_onmessage_widget(self, ctx):
        try:
            enabled = await self.config.guild(ctx.guild).on_message()
            return WidgetData.kpi(value="An" if enabled else "Aus", label="WoW Forever Auto-Reply")
        except Exception:
            return WidgetData.kpi(value="–", label="WoW Forever Auto-Reply")

    # --- Guild panel: auto-reply & channels ------------------------------ #
    @dashboard_panel("settings", L("WoWTools Forever (Server)", "WoWTools Forever (Server)"), mount="guild_settings", permission="guild_admin")
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
        ]
        return PanelSchema(
            fields=[
                Field.switch("on_message", "Auto-reply to WoW terms", value=bool(await g.on_message())),
                Field.select("region", "Region", region_opts, value=str(await g.region() or "")),
                Field.text("gmanage_guild", "Guild name (in-game)", value=str(await self.get_wow_guild_name(ctx.guild) or "")),
                Field.select("guild_log_channel", "Log channel", ch_opts, value=str(await g.guild_log_channel() or "")),
                Field.textarea("welcome_note", "Welcome text", value=str(dt.get("welcome_note", "")), variables=text_vars),
                Field.textarea("status_note", "Status text", value=str(dt.get("status_note", "")), variables=text_vars),
            ]
        )

    @wowtools_guild_panel.on_submit
    async def _save_wowtools_guild(self, ctx, data):
        g = self.config.guild(ctx.guild)
        hint = ""
        if "on_message" in data:
            await g.on_message.set(bool(data["on_message"]))
            if data["on_message"]:
                hint = await self.onmessage_conflict_hint(ctx.guild, await g.language())
        if "region" in data:
            v = str(data["region"]).lower().strip()
            await g.region.set(v if v in ("eu", "us", "kr") else None)
        # "real_guild_name" is accepted for forms rendered by an older version.
        for key in ("gmanage_guild", "real_guild_name"):
            if key in data:
                await self.set_wow_guild_name(ctx.guild, str(data[key] or ""))
                break
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
        # "countdown_channel" is ignored on purpose (no countdown in WoW Forever).
        saved = tr(ctx, "Gespeichert.", "Saved.")
        return SubmitResult.ok(f"{saved} {hint}" if hint else saved)

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
        return PanelSchema(
            description=tr(ctx, "Geteilte API-Tokens (Blizzard Battle.net).", "Shared API tokens (Blizzard Battle.net)."),
            fields=[
                Field.text("blizzard_client_id", "Blizzard Client ID", value=bliz.get("client_id", "")),
                Field.text("blizzard_client_secret", "Blizzard Client Secret", value=bliz.get("client_secret", "")),
            ],
        )

    @wowtools_api_panel.on_submit
    async def _save_wowtools_api(self, ctx, data):
        await self.bot.set_shared_api_tokens(
            "blizzard",
            client_id=str(data.get("blizzard_client_id", "")).strip(),
            client_secret=str(data.get("blizzard_client_secret", "")).strip(),
        )
        try:
            await self.create_bnet_objs()
        except Exception:
            pass
        return SubmitResult.ok(tr(ctx, "API-Tokens gespeichert.", "API tokens saved."))

    async def cog_unload(self):
        unregister_dashboard(self)
        self.bot.loop.create_task(self.session.close())
        self.guild_log.cancel()
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
        if cog.qualified_name not in {"Dashboard", "WebDashboard", "pdc_webdashboard"}:
            return
        try:
            cog.rpc.third_parties_handler.add_third_party(self, overwrite=True)  # type: ignore[attr-defined]
            self._dashboard_attached = True
        except Exception:
            self._dashboard_attached = False

    @_dashboard_page(name=None, description="WoWTools Forever Dashboard")
    async def dashboard_home(self, **kwargs: Any) -> Dict[str, Any]:
        _ = kwargs
        source = """
<div style="padding: 12px;">
  <h2>WoWTools Forever</h2>
  <p>Dashboard integration is active.</p>
  <p>Use the page <b>wowtoolsforever</b> for guild-specific settings.</p>
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
        name="wowtoolsforever",
        description="Guild-side WoWTools Forever settings and text defaults.",
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
        gname = await self.get_wow_guild_name(guild)
        on_message = await gconf.on_message()
        texts = await gconf.dashboard_texts()

        if method.upper() == "POST" and data:
            form = dict(data.get("form", {}))
            new_region = str(form.get("region", region or "")).lower().strip()
            if new_region in ("eu", "us", "kr"):
                await gconf.region.set(new_region)
            await self.set_wow_guild_name(guild, str(form.get("guild_name", gname or "")))
            new_on_message = str(form.get("on_message", "off")).lower() in ("on", "true", "1", "yes")
            await gconf.on_message.set(new_on_message)
            texts["welcome_note"] = str(form.get("welcome_note", texts.get("welcome_note", "")))
            texts["status_note"] = str(form.get("status_note", texts.get("status_note", "")))
            await gconf.dashboard_texts.set(texts)
            notifications = [{"message": "WoWTools Forever dashboard settings saved.", "category": "success"}]
            if new_on_message:
                hint = await self.onmessage_conflict_hint(guild, await gconf.language())
                if hint:
                    notifications.append({"message": hint, "category": "warning"})
            return {
                "status": 0,
                "notifications": notifications,
                "redirect_url": kwargs.get("request_url"),
            }

        # Escape ALL user-controlled values before interpolating them into the
        # HTML below (attributes and element content) to prevent stored XSS.
        region_esc = html.escape(str(region or ""), quote=True)
        gname_esc = html.escape(str(gname or ""), quote=True)
        welcome_note_esc = html.escape(str(texts.get("welcome_note", "")), quote=True)
        status_note_esc = html.escape(str(texts.get("status_note", "")), quote=True)

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
<h2>WoWTools Forever - Guild Dashboard</h2>
<p><b>Variables:</b> <code>{{guild_name}}</code> <code>{{region}}</code></p>
<form method='post'>
<label>Region</label><select name='region'>
<option value='eu' {'selected' if (region_esc or 'eu') == 'eu' else ''}>eu</option>
<option value='us' {'selected' if region_esc == 'us' else ''}>us</option>
<option value='kr' {'selected' if region_esc == 'kr' else ''}>kr</option>
</select><br><br>
<label>Guild Name</label><input name='guild_name' value='{gname_esc}'><br><br>
<label><input type='checkbox' name='on_message' {'checked' if on_message else ''}> Enable on_message feature</label><br><br>
<label>Welcome note template</label><textarea rows='2' name='welcome_note'>{welcome_note_esc}</textarea><br><br>
<label>Status note template</label><textarea rows='2' name='status_note'>{status_note_esc}</textarea><br><br>
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

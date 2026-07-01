from typing import Any, Dict, List, Optional

import html
import json
import traceback
import asyncio
import time
from datetime import datetime, timezone

from discord.ext import tasks

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

try:
    # Late-bound by Dashboard when registering third-party pages.
    from pdc_dashboard.rpc.third_parties import dashboard_page as _dashboard_page  # type: ignore
except Exception:
    try:
        from dashboard.rpc.third_parties import dashboard_page as _dashboard_page  # type: ignore
    except Exception:
        def _dashboard_page(*args: Any, **kwargs: Any):  # type: ignore
            def decorator(func: Any) -> Any:
                # Dashboard detects this marker and wraps it with its own decorator.
                func.__dashboard_decorator_params__ = (args, kwargs)
                return func
            return decorator

from .automation.new_user import handle_new_member_onboarding
from .character_helpers import (
    GAME_MOP,
    GAME_RETAIL,
    char_tuple_key,
    clear_main_for_game,
    set_main_for_game,
    find_char_owner_guild_wide,
    format_char_line,
    format_mains_summary,
    format_rank_sync_summary,
    game_label,
    get_linked_list,
    get_main_characters,
    mains_from_member_data,
    merge_onboarding_character_into_linked,
    merge_rank_sync_game_state,
    normalize_linked_characters,
    profile_key_to_link_game,
    set_linked_list,
    wow_profile_for_game,
)
from .officer_notifications import send_rank_lock_officer_notice, send_protected_rank_officer_notice
from .character_ui import (
    ADMIN_PANEL_INTRO,
    CharMainMenuView,
    LinkedRemovePageView,
    OfficerListMenuView,
    PANEL_INTRO,
    SlashWowAdminSyncAllConfirmView,
    WowAdminCharPanelView,
    officer_can_manage_characters,
    _panel_intro,
    _admin_panel_intro,
)
from .readytimes_ui import (
    format_member_ready_times_block,
    member_marked_any_day,
    send_member_readytimes_panel,
)
from .slash_modals import (
    AdminPickOneMemberView,
    BotSetupModal,
    GuildSettingsModal,
    MapRankModal,
    MasterSetupModal,
    OnboardingSetupModal,
    RankLockAddModal,
    RankLockRemoveModal,
    SetRankTitleModal,
    SyncIntervalModal,
)
from .functions.automations import RankSyncService
from .functions.blizzard import BlizzardService
from .pdc_dashboard import (
    dashboard_widget,
    dashboard_panel,
    dashboard_list,
    WidgetData,
    PanelSchema,
    Field,
    SubmitResult,
    L,
    tr,
    tr_lang,
    register_dashboard,
    unregister_dashboard,
)

# Invisible sentinel prefix marking the "no main for a configured profile" report
# (lets internal checks stay language-independent while output is localized).
_NO_MAIN_MARKER = "​"

I18N = {
    "de-DE": {
        "server_only": "Nur auf einem Server nutzbar.",
        "wow_help": "Nutze Unterbefehle wie `wow guildsettings` oder `wow chars`.",
        "readytimes_init": "Bereitschaftszeiten-Editor ist initialisiert. Der nächste Schritt wäre ein Modal/Button-UI pro Wochentag.",
        "settings_saved": "Guild-Setup gespeichert: `{region}/{version}/{realm}` - `{guild}`",
        "chars_none": "Keine Chars verlinkt.",
        "char_added": "Char `{char}` hinzugefügt.",
        "char_removed": "Char `{char}` entfernt.",
        "chars_invalid": "Ungültig. Benutze action: `list`, `add`, `remove`.",
        "rank_synced": "Rang erfolgreich synchronisiert: `{rank}`",
        "rank_synced_multi": "Rang-Sync:\n{lines}",
        "rank_failed": "Mainchar nicht gefunden oder API nicht konfiguriert.",
        "rank_sync_locked": "Rang-Sync für **{game}** ist eingefroren — Discord-Rolle unverändert.",
        "rank_sync_no_profile": "Kein WoW-Profil für **{game}** auf diesem Server.",
        "rank_freeze_ok": "Rang-Sync für **{game}** ist eingefroren. Manuelle Rollen bleiben erhalten bis `wow rank-unfreeze`.",
        "rank_unfreeze_ok": "Rang-Sync für **{game}** wieder aktiv.",
        "botsetup_saved": "Bot-Setup gespeichert.",
        "master_saved": "Master-Setup gespeichert.",
        "onboarding_setup_intro": "Onboarding-Setup gestartet. Antworte pro Schritt im Chat.",
        "onboarding_setup_mode": "Soll der Bot Channel/Rollen erstellen? Antworte mit `create` oder `existing`.",
        "onboarding_setup_done": "Onboarding-Setup gespeichert. Channel: {channel}, Rollen: new={new_role}, complete={complete_role}",
        "onboarding_setup_cancelled": "Setup abgebrochen oder ungültige Eingabe.",
        "prompt_new_role": "Sende die Rollen-ID fuer `onboarding-new` (oder `skip`).",
        "prompt_complete_role": "Sende die Rollen-ID fuer `onboarding-complete` (oder `skip`).",
        "prompt_channel": "Sende die Channel-ID fuer den Onboarding-Channel (oder `skip`).",
    },
    "en-US": {
        "server_only": "This command can only be used in a server.",
        "wow_help": "Use subcommands like `wow guildsettings` or `wow chars`.",
        "readytimes_init": "Ready-times editor initialized. Next step is a modal/button UI for each weekday.",
        "settings_saved": "Guild setup saved: `{region}/{version}/{realm}` - `{guild}`",
        "chars_none": "No characters linked.",
        "char_added": "Character `{char}` added.",
        "char_removed": "Character `{char}` removed.",
        "chars_invalid": "Invalid action. Use: `list`, `add`, `remove`.",
        "rank_synced": "Rank synchronized successfully: `{rank}`",
        "rank_synced_multi": "Rank sync:\n{lines}",
        "rank_failed": "Main character not found or API not configured.",
        "rank_sync_locked": "Rank sync for **{game}** is frozen — Discord role unchanged.",
        "rank_sync_no_profile": "No WoW profile configured for **{game}** on this server.",
        "rank_freeze_ok": "Rank sync for **{game}** is frozen. Manual roles stay until `wow rank-unfreeze`.",
        "rank_unfreeze_ok": "Rank sync for **{game}** is active again.",
        "botsetup_saved": "Bot setup saved.",
        "master_saved": "Master setup saved.",
        "onboarding_setup_intro": "Onboarding setup started. Reply to each step in this channel.",
        "onboarding_setup_mode": "Should the bot create channel/roles? Reply with `create` or `existing`.",
        "onboarding_setup_done": "Onboarding setup saved. Channel: {channel}, Roles: new={new_role}, complete={complete_role}",
        "onboarding_setup_cancelled": "Setup cancelled or invalid input.",
        "prompt_new_role": "Send the role ID for `onboarding-new` (or `skip`).",
        "prompt_complete_role": "Send the role ID for `onboarding-complete` (or `skip`).",
        "prompt_channel": "Send the channel ID for the onboarding channel (or `skip`).",
    },
}


class WowGuildAutomation(commands.Cog):
    """WoW guild onboarding and role automation for Red."""

    # Minimum interval between officer notices for a locked in-game rank (auto-sync).
    RANK_LOCK_NOTICE_COOLDOWN_SEC = 6 * 3600

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=980231234, force_registration=True)
        self.config.register_global(
            bot_setup={
                "client_id": "",
                "client_secret": "",
                "owner_ids": [],
                "default_language": "en-US",
                "default_region": "eu",
                "default_version": "retail",
                "dashboard_enabled": True,
            }
        )
        self.config.register_guild(
            language="en-US",
            active_profile_key="retail",
            features={
                "onboarding": True,
                "auto_verify": True,
                "ready_times": True,
                "sync_rank": True,
                "allied_guilds": False,
            },
            allied_guilds=[],
            wow={"region": "eu", "version": "retail", "realm": "", "guild_name": ""},
            wow_profiles={
                "retail": {"region": "eu", "version": "retail", "realm": "", "guild_name": ""}
            },
            onboarding={
                "welcome_text_de": "Willkommen beim Onboarding!",
                "welcome_text_en": "Welcome to onboarding!",
            },
            roles={
                "guest_role_id": 0,
                "member_role_id": 0,
                "onboarding_new_role_id": 0,
                "onboarding_complete_role_id": 0,
                "allied_role_id": 0,
            },
            rank_mapping={},
            rank_titles={},
            rank_mapping_by_profile={},
            rank_titles_by_profile={},
            protected_rank_titles_by_profile={},
            locked_rank_titles_by_profile={},
            channels={
                "onboarding_channel_id": 0,
                "manual_review_channel_id": 0,
                "raid_guest_channel_id": 0,
                "officer_character_notify_channel_id": 0,
                "rank_protected_notify_channel_id": 0,
                "rank_lock_notify_channel_id": 0,
            },
            rules={"rule_channel_id": 0, "rule_emoji": "✅"},
            rank_sync_interval_minutes=0,
            rank_sync_last_run_epoch=0,
            templates={
                "manual_verification": "Manuelle Verifizierung nötig! User {username} hat sich gemeldet als Char {charname} und möchte Gildenrechte erhalten. Bitte bestätigen sie dies manuell.",
                "duplicate_character_message": "Dieser Charakter ist bereits verknüpft oder ungültig. Wende dich an einen Offizier. ({detail})",
                "member_left_characters_notice": "Mitglied {user} hat den Server verlassen. Verknüpfte Chars: {chars}",
                "admin_removed_char_dm": "Ein Offizier hat folgende WoW-Chars von dir entfernt: {chars}\nGrund: {reason}",
                "protected_rank_sync_notice": (
                    "{member} — **{game}**, Main `{char}`: Ingame-Rang **{rank}** ist geschützt; "
                    "kein automatischer Discord-Rang-Sync."
                ),
                "rank_lock_officer_notice": (
                    "{member} — **{game}**, Main `{char}`: Ingame-Rang **{rank}** steht auf der **Rank-Lock**-Liste; "
                    "Bot setzt keine Discord-Rangrolle.{detail}"
                ),
            },
        )
        self.config.register_member(
            chars=[],
            linked_characters=[],
            main_character=None,
            # Only persist real dict entries — Red nested_update fails on None leaves.
            main_characters={},
            ready_times={},
            onboarding_language="en-US",
            selected_game="retail",
            registration={},
            onboarding_session_id="",
            rank_sync_by_game={},
        )
        self.blizzard = BlizzardService()
        self.rank_sync = RankSyncService(self.blizzard)
        self._dashboard_attached = False

    def _attach_to_dashboard(self, dashboard_cog: commands.Cog) -> bool:
        try:
            dashboard_cog.rpc.third_parties_handler.add_third_party(self, overwrite=True)  # type: ignore[attr-defined]
            return True
        except TypeError:
            # Backward compatibility for Dashboard versions without overwrite kwarg.
            try:
                dashboard_cog.rpc.third_parties_handler.add_third_party(self)  # type: ignore[attr-defined]
                return True
            except Exception:
                return False
        except Exception:
            return False

    def _get_dashboard_cog(self) -> Optional[commands.Cog]:
        return self.bot.get_cog("pdc_webdashboard") or self.bot.get_cog("WebDashboard") or self.bot.get_cog("Dashboard")

    async def cog_load(self) -> None:
        bot_setup = await self.config.bot_setup()
        self.blizzard.client_id = bot_setup.get("client_id", "")
        self.blizzard.client_secret = bot_setup.get("client_secret", "")
        dashboard_cog = self._get_dashboard_cog()
        if dashboard_cog is not None:
            self._dashboard_attached = self._attach_to_dashboard(dashboard_cog)
        try:
            if not self._rank_auto_sync_loop.is_running():
                self._rank_auto_sync_loop.start()
        except Exception:
            pass
        register_dashboard(self)

    async def cog_unload(self) -> None:
        unregister_dashboard(self)
        try:
            self._rank_auto_sync_loop.cancel()
        except Exception:
            pass
        dashboard_cog = self._get_dashboard_cog()
        if dashboard_cog is not None:
            try:
                dashboard_cog.rpc.third_parties_handler.remove_third_party(self)  # type: ignore[attr-defined]
            except Exception:
                pass
        self._dashboard_attached = False

    @commands.Cog.listener()
    async def on_cog_add(self, cog: commands.Cog) -> None:
        if self._dashboard_attached:
            return
        if cog.qualified_name not in {"Dashboard", "WebDashboard", "pdc_webdashboard"}:
            return
        self._dashboard_attached = self._attach_to_dashboard(cog)

    @dashboard_widget("wga_onboarding", L("Onboarding", "Onboarding"), size="sm", permission="guild_member")
    async def wga_onboarding_widget(self, ctx):
        try:
            features = await self.config.guild(ctx.guild).features()
            enabled = bool((features or {}).get("onboarding", True))
            return WidgetData.kpi(value="An" if enabled else "Aus", label="Onboarding")
        except Exception:
            return WidgetData.kpi(value="–", label="Onboarding")

    # --- Global panel (bot owner): Blizzard API & defaults ---------------- #
    @dashboard_panel(
        "blizzard_api", L("Blizzard API & Defaults", "Blizzard API & defaults"),
        scope="global", mount="bot_settings", permission="bot_owner",
    )
    async def wga_global_panel(self, ctx):
        s = await self.config.bot_setup()
        return PanelSchema(
            description=tr(ctx, "Globale WoW-Guild-Automation-Einstellungen (Blizzard API).", "Global WoW Guild Automation settings (Blizzard API)."),
            fields=[
                Field.text("client_id", "Blizzard Client ID", value=s.get("client_id", "")),
                Field.text("client_secret", "Blizzard Client Secret", value=s.get("client_secret", "")),
                Field.select(
                    "default_region", "Default region",
                    [{"value": "eu", "label": "EU"}, {"value": "us", "label": "US"}, {"value": "kr", "label": "KR"}],
                    value=s.get("default_region", "eu"),
                ),
                Field.select(
                    "default_version", "Default version",
                    [{"value": "retail", "label": "Retail"}, {"value": "classic", "label": "Classic"}],
                    value=s.get("default_version", "retail"),
                ),
                Field.text("default_language", "Default language", value=s.get("default_language", "de-DE")),
            ],
        )

    @wga_global_panel.on_submit
    async def _save_wga_global(self, ctx, data):
        s = await self.config.bot_setup()
        for k in ("client_id", "client_secret", "default_region", "default_version", "default_language"):
            if k in data:
                s[k] = str(data[k]).strip()
        await self.config.bot_setup.set(s)
        try:
            self.blizzard.client_id = s.get("client_id", "")
            self.blizzard.client_secret = s.get("client_secret", "")
        except Exception:
            pass
        return SubmitResult.ok("Gespeichert.")

    # --- Guild panel: add allied guild ----------------------------------- #
    @dashboard_panel(
        "allied_add", L("Verbündete Gilde hinzufügen", "Add allied guild"), mount="guild_settings", permission="guild_admin",
    )
    async def wga_allied_add_panel(self, ctx):
        return PanelSchema(
            description=tr(ctx, "Gildennamen eingeben und speichern, um ihn zur Liste hinzuzufügen.", "Enter a guild name and save to add it to the list."),
            fields=[Field.text("guild_name", "Guild name", value="", placeholder="z. B. Meine-Gilde")],
            submit_label="Hinzufügen",
        )

    @wga_allied_add_panel.on_submit
    async def _wga_allied_add(self, ctx, data):
        name = str(data.get("guild_name", "")).strip()
        if not name:
            return SubmitResult.fail("Bitte einen Gildennamen eingeben.")
        async with self.config.guild(ctx.guild).allied_guilds() as gl:
            if name in gl:
                return SubmitResult.fail("Gilde ist bereits in der Liste.")
            gl.append(name)
        return SubmitResult.ok("Gilde hinzugefügt.")

    # --- Guild list: allied guilds (view/delete) ------------------------- #
    @dashboard_list(
        "allied_guilds", L("Verbündete Gilden", "Allied guilds"), mount="guild_settings", permission="guild_admin",
        columns=[{"key": "guild", "label": "Guild"}],
        description=L("Verbündete Gilden, die abgefragt werden. Hinzufügen über das Hinzufügen-Tab.", "Allied guilds that are queried. Add via the Add tab."),
    )
    async def wga_allied_list(self, ctx):
        guilds = await self.config.guild(ctx.guild).allied_guilds()
        return [{"id": str(g), "cells": {"guild": str(g)}} for g in (guilds or [])]

    @wga_allied_list.on_delete
    async def _wga_allied_delete(self, ctx, item_id):
        async with self.config.guild(ctx.guild).allied_guilds() as gl:
            matches = [g for g in gl if str(g) == str(item_id)]
            if not matches:
                return SubmitResult.fail("Eintrag nicht gefunden.")
            for g in matches:
                gl.remove(g)
        return SubmitResult.ok("Verbündete Gilde entfernt.")

    # --- Dashboard helper: channel/role options -------------------------- #
    def _wga_channel_options(self, ctx, *, with_none: bool = True) -> List[Dict[str, str]]:
        opts: List[Dict[str, str]] = []
        if with_none:
            opts.append({"value": "", "label": "— none —"})
        try:
            for ch in ctx.guild.text_channels:
                opts.append({"value": str(ch.id), "label": f"#{ch.name}"})
        except Exception:
            pass
        return opts

    def _wga_role_options(self, ctx, *, with_none: bool = True) -> List[Dict[str, str]]:
        opts: List[Dict[str, str]] = []
        if with_none:
            opts.append({"value": "", "label": "— none —"})
        try:
            for role in ctx.guild.roles:
                if role.is_default():
                    continue
                opts.append({"value": str(role.id), "label": role.name})
        except Exception:
            pass
        return opts

    async def _wga_active_profile(self, ctx) -> str:
        try:
            prof = await self.config.guild(ctx.guild).active_profile_key()
        except Exception:
            prof = ""
        return str(prof or "retail")

    # --- Guild panel: profile switcher (leftmost, switch only) ----------- #
    @dashboard_panel(
        "wga_switch", L("Profil wechseln", "Switch profile"), mount="guild_settings", permission="guild_admin", order=0,
    )
    async def wga_switch_panel(self, ctx):
        prof = await self._wga_active_profile(ctx)
        profiles = await self.config.guild(ctx.guild).wow_profiles()
        if not isinstance(profiles, dict):
            profiles = {}
        profile_options = [{"value": k, "label": k} for k in profiles.keys()] or [
            {"value": prof, "label": prof}
        ]
        return PanelSchema(
            description=tr(ctx, "Aktives WoW-Profil dieses Servers wählen. Bearbeitet und angelegt wird im Tab „Profil“.", "Select this server's active WoW profile. Editing and creating happens in the “Profile” tab."),
            submit_label="Wechseln",
            fields=[
                Field.select("active_profile_key", "Profile", profile_options, value=prof),
            ],
        )

    @wga_switch_panel.on_submit
    async def _wga_switch_submit(self, ctx, data):
        profiles = await self.config.guild(ctx.guild).wow_profiles()
        if not isinstance(profiles, dict):
            profiles = {}
        selected = str(data.get("active_profile_key", "")).strip()
        if selected and selected in profiles:
            await self.config.guild(ctx.guild).active_profile_key.set(selected)
            # reload=True: die Tabs "Profil" und "Profile" sollen den Wechsel sofort zeigen.
            return SubmitResult.ok(f"Profil „{selected}“ aktiviert.", reload=True)
        return SubmitResult.fail("Unbekanntes Profil.")

    # --- Guild panel: profile (edit/create active profile) --------------- #
    @dashboard_panel(
        "wga_profile", L("Profil", "Profile"), mount="guild_settings", permission="guild_admin", order=1,
    )
    async def wga_profile_panel(self, ctx):
        language = await self.config.guild(ctx.guild).language()
        prof = await self._wga_active_profile(ctx)
        profiles = await self.config.guild(ctx.guild).wow_profiles()
        if not isinstance(profiles, dict):
            profiles = {}
        active = profiles.get(prof, {})
        if not isinstance(active, dict):
            active = {}
        version_options = [
            {"value": "retail", "label": "Retail"},
            {"value": "classic", "label": "Classic"},
            {"value": "classic_era", "label": "Classic Era"},
            {"value": "mop_classic", "label": "MoP Classic"},
            {"value": "sod", "label": "SoD"},
        ]
        return PanelSchema(
            description=tr(ctx,
                        f"Sprache und Werte des aktiven Profils „{prof}“ bearbeiten. "
                        "Profil wechseln über den Tab „Profil wechseln“.",
                        f"Edit the language and values of the active profile “{prof}”. "
                        "Switch profiles via the “Switch profile” tab."),
            fields=[
                Field.select(
                    "language", "Language",
                    [{"value": "de-DE", "label": "Deutsch"}, {"value": "en-US", "label": "English"}],
                    value=str(language or "de-DE"),
                ),
                Field.text(
                    "new_profile_key", "New profile (key)", value="",
                    placeholder="leer lassen wenn nicht neu",
                    description="Leave empty if no new profile should be created.",
                ),
                Field.text("region", "Region", value=str(active.get("region", "eu"))),
                Field.select("version", "Version", version_options, value=str(active.get("version", "retail"))),
                Field.text("realm", "Realm", value=str(active.get("realm", ""))),
                Field.text("guild_name", "Guild name", value=str(active.get("guild_name", ""))),
            ],
        )

    @wga_profile_panel.on_submit
    async def _wga_profile_submit(self, ctx, data):
        await self.config.guild(ctx.guild).language.set(str(data.get("language", "de-DE")).strip() or "de-DE")
        new_key = str(data.get("new_profile_key", "")).strip()
        region = str(data.get("region", "")).strip()
        version = str(data.get("version", "retail")).strip() or "retail"
        realm = str(data.get("realm", "")).strip()
        guild_name = str(data.get("guild_name", "")).strip()
        current = await self._wga_active_profile(ctx)
        msg = "Profil gespeichert."
        async with self.config.guild(ctx.guild).wow_profiles() as profiles:
            if not isinstance(profiles, dict):
                profiles.clear()
            if new_key:
                # Create new profile + set active.
                profiles[new_key] = {
                    "region": region or "eu", "version": version, "realm": realm, "guild_name": guild_name,
                }
                active_key = new_key
                msg = f"Profil '{new_key}' angelegt und aktiviert."
            else:
                # Edit the currently active profile (switching happens in the
                # dedicated "Profil wechseln" tab only).
                active_key = current
                entry = profiles.get(active_key)
                if not isinstance(entry, dict):
                    entry = {}
                entry.update(
                    {"region": region, "version": version, "realm": realm, "guild_name": guild_name}
                )
                profiles[active_key] = entry
        await self.config.guild(ctx.guild).active_profile_key.set(active_key)
        # Anlegen/Wechsel wirkt sich auf die Liste & den Wechsel-Tab aus → neu laden.
        return SubmitResult.ok(msg, reload=True)

    # --- Guild list: WoW profiles (view/edit/delete all) ----------------- #
    @dashboard_list(
        "wga_profiles", L("Profile", "Profiles"), mount="guild_settings", permission="guild_admin", order=2,
        columns=[
            {"key": "key", "label": "Profile"},
            {"key": "version", "label": "Version"},
            {"key": "realm", "label": "Realm"},
            {"key": "guild", "label": "Guild"},
            {"key": "active", "label": "Active"},
        ],
        description=L("Alle WoW-Profile dieses Servers. Bearbeiten oder löschen pro Zeile; Anlegen im Tab Profil.", "All WoW profiles of this server. Edit or delete per row; create in the Profile tab."),
    )
    async def wga_profiles_list(self, ctx):
        profiles = await self.config.guild(ctx.guild).wow_profiles()
        active = await self._wga_active_profile(ctx)
        if not isinstance(profiles, dict):
            profiles = {}
        rows = []
        for key, p in profiles.items():
            p = p if isinstance(p, dict) else {}
            rows.append({"id": str(key), "cells": {
                "key": str(key),
                "version": str(p.get("version", "")),
                "realm": str(p.get("realm", "") or "—"),
                "guild": str(p.get("guild_name", "") or "—"),
                "active": "✅" if key == active else "",
            }})
        return rows

    @wga_profiles_list.edit_form
    async def _wga_profiles_edit_form(self, ctx, item_id):
        profiles = await self.config.guild(ctx.guild).wow_profiles()
        p = (profiles.get(str(item_id)) or {}) if isinstance(profiles, dict) else {}
        if not isinstance(p, dict):
            p = {}
        active = await self._wga_active_profile(ctx)
        version_options = [
            {"value": "retail", "label": "Retail"},
            {"value": "classic", "label": "Classic"},
            {"value": "classic_era", "label": "Classic Era"},
            {"value": "mop_classic", "label": "MoP Classic"},
            {"value": "sod", "label": "SoD"},
        ]
        return PanelSchema(
            description=tr(ctx, f"Profil '{item_id}' bearbeiten.", f"Edit profile '{item_id}'."),
            fields=[
                Field.switch("active", "Set as active profile", value=(str(item_id) == active)),
                Field.text("region", "Region", value=str(p.get("region", "eu"))),
                Field.select("version", "Version", version_options, value=str(p.get("version", "retail"))),
                Field.text("realm", "Realm", value=str(p.get("realm", ""))),
                Field.text("guild_name", "Guild name", value=str(p.get("guild_name", ""))),
            ],
        )

    @wga_profiles_list.on_edit
    async def _wga_profiles_edit(self, ctx, item_id, data):
        async with self.config.guild(ctx.guild).wow_profiles() as profiles:
            if not isinstance(profiles, dict):
                return SubmitResult.fail("Keine Profile vorhanden.")
            entry = profiles.get(str(item_id))
            if not isinstance(entry, dict):
                entry = {}
            entry.update({
                "region": str(data.get("region", "")).strip() or "eu",
                "version": str(data.get("version", "retail")).strip() or "retail",
                "realm": str(data.get("realm", "")).strip(),
                "guild_name": str(data.get("guild_name", "")).strip(),
            })
            profiles[str(item_id)] = entry

        if bool(data.get("active", False)):
            await self.config.guild(ctx.guild).active_profile_key.set(str(item_id))

        return SubmitResult.ok("Profil aktualisiert.")

    @wga_profiles_list.on_delete
    async def _wga_profiles_delete(self, ctx, item_id):
        key = str(item_id)
        async with self.config.guild(ctx.guild).wow_profiles() as profiles:
            if not isinstance(profiles, dict) or key not in profiles:
                return SubmitResult.fail("Profil nicht gefunden.")
            if len(profiles) <= 1:
                return SubmitResult.fail("Das letzte Profil kann nicht gelöscht werden.")
            profiles.pop(key, None)
        # If the active profile was deleted, switch to another one.
        active = await self._wga_active_profile(ctx)
        if active == key:
            remaining = await self.config.guild(ctx.guild).wow_profiles()
            if isinstance(remaining, dict) and remaining:
                await self.config.guild(ctx.guild).active_profile_key.set(next(iter(remaining.keys())))
        return SubmitResult.ok("Profil gelöscht.")

    # --- Guild panel: onboarding ----------------------------------------- #
    @dashboard_panel(
        "wga_onboarding_cfg", L("Onboarding", "Onboarding"), mount="guild_settings", permission="guild_admin",
    )
    async def wga_onboarding_cfg_panel(self, ctx):
        onboarding = await self.config.guild(ctx.guild).onboarding()
        roles = await self.config.guild(ctx.guild).roles()
        channels = await self.config.guild(ctx.guild).channels()
        features = await self.config.guild(ctx.guild).features()
        if not isinstance(onboarding, dict):
            onboarding = {}
        if not isinstance(roles, dict):
            roles = {}
        if not isinstance(channels, dict):
            channels = {}
        if not isinstance(features, dict):
            features = {}
        return PanelSchema(
            description=tr(ctx, "Onboarding-Texte, Channel, Rollen und Feature-Schalter.", "Onboarding texts, channel, roles and feature toggles."),
            fields=[
                Field.textarea(
                    "welcome_text_de", "Welcome text (DE)",
                    value=str(onboarding.get("welcome_text_de", "")),
                ),
                Field.textarea(
                    "welcome_text_en", "Welcome text (EN)",
                    value=str(onboarding.get("welcome_text_en", "")),
                ),
                Field.select(
                    "onboarding_channel", "Onboarding channel",
                    self._wga_channel_options(ctx),
                    value=str(channels.get("onboarding_channel_id", 0) or ""),
                ),
                Field.select(
                    "onboarding_new_role", "Role: new onboarding",
                    self._wga_role_options(ctx),
                    value=str(roles.get("onboarding_new_role_id", 0) or ""),
                ),
                Field.select(
                    "onboarding_complete_role", "Role: onboarding complete",
                    self._wga_role_options(ctx),
                    value=str(roles.get("onboarding_complete_role_id", 0) or ""),
                ),
                Field.switch(
                    "feature_onboarding", "Feature: onboarding enabled",
                    value=bool(features.get("onboarding", True)),
                ),
            ],
        )

    @wga_onboarding_cfg_panel.on_submit
    async def _wga_onboarding_cfg_submit(self, ctx, data):
        async with self.config.guild(ctx.guild).onboarding() as ob:
            if not isinstance(ob, dict):
                ob.clear()
            ob["welcome_text_de"] = str(data.get("welcome_text_de", "")).strip()
            ob["welcome_text_en"] = str(data.get("welcome_text_en", "")).strip()
        async with self.config.guild(ctx.guild).roles() as rl:
            if not isinstance(rl, dict):
                rl.clear()
            rl["onboarding_new_role_id"] = int(data["onboarding_new_role"]) if str(data.get("onboarding_new_role", "")).strip() else 0
            rl["onboarding_complete_role_id"] = int(data["onboarding_complete_role"]) if str(data.get("onboarding_complete_role", "")).strip() else 0
        async with self.config.guild(ctx.guild).channels() as ch:
            if not isinstance(ch, dict):
                ch.clear()
            ch["onboarding_channel_id"] = int(data["onboarding_channel"]) if str(data.get("onboarding_channel", "")).strip() else 0
        async with self.config.guild(ctx.guild).features() as ft:
            if not isinstance(ft, dict):
                ft.clear()
            ft["onboarding"] = bool(data.get("feature_onboarding", False))
        return SubmitResult.ok("Onboarding-Einstellungen gespeichert.")

    # --- Guild panel: rules ---------------------------------------------- #
    @dashboard_panel(
        "wga_rules", L("Rules", "Rules"), mount="guild_settings", permission="guild_admin",
    )
    async def wga_rules_panel(self, ctx):
        rules = await self.config.guild(ctx.guild).rules()
        if not isinstance(rules, dict):
            rules = {}
        return PanelSchema(
            description=tr(ctx, "Regel-Channel und Reaktions-Emoji für die Regelbestätigung.", "Rules channel and reaction emoji for rule confirmation."),
            fields=[
                Field.select(
                    "rule_channel", "Rules channel",
                    self._wga_channel_options(ctx),
                    value=str(rules.get("rule_channel_id", 0) or ""),
                ),
                Field.text("rule_emoji", "Rules emoji", value=str(rules.get("rule_emoji", "✅"))),
            ],
        )

    @wga_rules_panel.on_submit
    async def _wga_rules_submit(self, ctx, data):
        async with self.config.guild(ctx.guild).rules() as rules:
            if not isinstance(rules, dict):
                rules.clear()
            rules["rule_channel_id"] = int(data["rule_channel"]) if str(data.get("rule_channel", "")).strip() else 0
            emoji = str(data.get("rule_emoji", "")).strip()
            rules["rule_emoji"] = emoji or "✅"
        return SubmitResult.ok("Regel-Einstellungen gespeichert.")

    # --- Guild panel: roles ---------------------------------------------- #
    @dashboard_panel(
        "wga_roles", L("Rollen", "Roles"), mount="guild_settings", permission="guild_admin",
    )
    async def wga_roles_panel(self, ctx):
        roles = await self.config.guild(ctx.guild).roles()
        features = await self.config.guild(ctx.guild).features()
        if not isinstance(roles, dict):
            roles = {}
        if not isinstance(features, dict):
            features = {}
        return PanelSchema(
            description=tr(ctx, "Basis-Rollen und Feature-Schalter.", "Base roles and feature toggles."),
            fields=[
                Field.select(
                    "guest_role", "Guest role", self._wga_role_options(ctx),
                    value=str(roles.get("guest_role_id", 0) or ""),
                ),
                Field.select(
                    "member_role", "Member role", self._wga_role_options(ctx),
                    value=str(roles.get("member_role_id", 0) or ""),
                ),
                Field.select(
                    "allied_role", "Allied role", self._wga_role_options(ctx),
                    value=str(roles.get("allied_role_id", 0) or ""),
                ),
                Field.switch(
                    "feature_allied", "Feature: allied guilds",
                    value=bool(features.get("allied_guilds", False)),
                ),
                Field.switch(
                    "feature_sync_rank", "Feature: rank sync",
                    value=bool(features.get("sync_rank", True)),
                ),
                Field.switch(
                    "feature_auto_verify", "Feature: auto-verification",
                    value=bool(features.get("auto_verify", True)),
                ),
                Field.switch(
                    "feature_ready_times", "Feature: ready times",
                    value=bool(features.get("ready_times", True)),
                ),
            ],
        )

    @wga_roles_panel.on_submit
    async def _wga_roles_submit(self, ctx, data):
        async with self.config.guild(ctx.guild).roles() as rl:
            if not isinstance(rl, dict):
                rl.clear()
            rl["guest_role_id"] = int(data["guest_role"]) if str(data.get("guest_role", "")).strip() else 0
            rl["member_role_id"] = int(data["member_role"]) if str(data.get("member_role", "")).strip() else 0
            rl["allied_role_id"] = int(data["allied_role"]) if str(data.get("allied_role", "")).strip() else 0
        async with self.config.guild(ctx.guild).features() as ft:
            if not isinstance(ft, dict):
                ft.clear()
            ft["allied_guilds"] = bool(data.get("feature_allied", False))
            ft["sync_rank"] = bool(data.get("feature_sync_rank", False))
            ft["auto_verify"] = bool(data.get("feature_auto_verify", False))
            ft["ready_times"] = bool(data.get("feature_ready_times", False))
        return SubmitResult.ok("Rollen & Features gespeichert.")

    # --- Guild panel: channels ------------------------------------------- #
    @dashboard_panel(
        "wga_channels", L("Channels", "Channels"), mount="guild_settings", permission="guild_admin",
    )
    async def wga_channels_panel(self, ctx):
        channels = await self.config.guild(ctx.guild).channels()
        if not isinstance(channels, dict):
            channels = {}
        return PanelSchema(
            description=tr(ctx, "Benachrichtigungs- und Review-Channels.", "Notification and review channels."),
            fields=[
                Field.select(
                    "manual_review_channel", "Manual review",
                    self._wga_channel_options(ctx),
                    value=str(channels.get("manual_review_channel_id", 0) or ""),
                ),
                Field.select(
                    "raid_guest_channel", "Raid guests",
                    self._wga_channel_options(ctx),
                    value=str(channels.get("raid_guest_channel_id", 0) or ""),
                ),
                Field.select(
                    "officer_character_notify_channel", "Officer: character notification",
                    self._wga_channel_options(ctx),
                    value=str(channels.get("officer_character_notify_channel_id", 0) or ""),
                ),
                Field.select(
                    "rank_protected_notify_channel", "Protected ranks: notification",
                    self._wga_channel_options(ctx),
                    value=str(channels.get("rank_protected_notify_channel_id", 0) or ""),
                ),
                Field.select(
                    "rank_lock_notify_channel", "Rank lock: notification",
                    self._wga_channel_options(ctx),
                    value=str(channels.get("rank_lock_notify_channel_id", 0) or ""),
                ),
            ],
        )

    @wga_channels_panel.on_submit
    async def _wga_channels_submit(self, ctx, data):
        mapping = {
            "manual_review_channel": "manual_review_channel_id",
            "raid_guest_channel": "raid_guest_channel_id",
            "officer_character_notify_channel": "officer_character_notify_channel_id",
            "rank_protected_notify_channel": "rank_protected_notify_channel_id",
            "rank_lock_notify_channel": "rank_lock_notify_channel_id",
        }
        async with self.config.guild(ctx.guild).channels() as ch:
            if not isinstance(ch, dict):
                ch.clear()
            for field_key, cfg_key in mapping.items():
                v = str(data.get(field_key, "")).strip()
                ch[cfg_key] = int(v) if v else 0
        return SubmitResult.ok("Channels gespeichert.")

    # --- Guild panel: texts (templates) ---------------------------------- #
    @dashboard_panel(
        "wga_templates", L("Texte", "Texts"), mount="guild_settings", permission="guild_admin",
    )
    async def wga_templates_panel(self, ctx):
        templates = await self.config.guild(ctx.guild).templates()
        if not isinstance(templates, dict):
            templates = {}
        rank_vars = [
            {"token": "{member}", "desc": "Member (mention)"},
            {"token": "{game}", "desc": "Game/version"},
            {"token": "{char}", "desc": "Character name"},
            {"token": "{rank}", "desc": "In-game rank"},
            {"token": "{profile}", "desc": "Profile key"},
            {"token": "{detail}", "desc": "Additional info"},
        ]
        return PanelSchema(
            description=tr(ctx, "Benachrichtigungs- und Hinweistexte. Platzhalter in geschweiften Klammern.", "Notification and notice texts. Placeholders in curly braces."),
            fields=[
                Field.textarea(
                    "duplicate_character_message", "Duplicate character",
                    value=str(templates.get("duplicate_character_message", "")),
                    variables=[{"token": "{detail}", "desc": "Additional info"}],
                ),
                Field.textarea(
                    "member_left_characters_notice", "Member left",
                    value=str(templates.get("member_left_characters_notice", "")),
                    variables=[
                        {"token": "{user}", "desc": "Member (mention)"},
                        {"token": "{username}", "desc": "Username"},
                        {"token": "{chars}", "desc": "Linked characters"},
                    ],
                ),
                Field.textarea(
                    "admin_removed_char_dm", "Admin removed character (DM)",
                    value=str(templates.get("admin_removed_char_dm", "")),
                    variables=[
                        {"token": "{chars}", "desc": "Removed characters"},
                        {"token": "{reason}", "desc": "Reason"},
                    ],
                ),
                Field.textarea(
                    "protected_rank_sync_notice", "Protected rank: notice",
                    value=str(templates.get("protected_rank_sync_notice", "")),
                    variables=rank_vars,
                ),
                Field.textarea(
                    "rank_lock_officer_notice", "Rank lock: officer notice",
                    value=str(templates.get("rank_lock_officer_notice", "")),
                    variables=rank_vars,
                ),
                Field.textarea(
                    "manual_verification", "Manual verification",
                    value=str(templates.get("manual_verification", "")),
                    variables=[
                        {"token": "{username}", "desc": "Username"},
                        {"token": "{charname}", "desc": "Character name"},
                    ],
                ),
            ],
        )

    @wga_templates_panel.on_submit
    async def _wga_templates_submit(self, ctx, data):
        keys = [
            "duplicate_character_message",
            "member_left_characters_notice",
            "admin_removed_char_dm",
            "protected_rank_sync_notice",
            "rank_lock_officer_notice",
            "manual_verification",
        ]
        async with self.config.guild(ctx.guild).templates() as tpl:
            if not isinstance(tpl, dict):
                tpl.clear()
            for k in keys:
                if k in data:
                    tpl[k] = str(data.get(k, ""))
        return SubmitResult.ok("Texte gespeichert.")

    # --- Guild list: rank mapping ---------------------------------------- #
    @dashboard_list(
        "wga_rank_mapping", L("Rank-Mapping", "Rank mapping"), mount="guild_settings", permission="guild_admin",
        columns=[
            {"key": "index", "label": "Index"},
            {"key": "title", "label": "Title"},
            {"key": "role", "label": "Discord role"},
        ],
        description=L("Rang 0–9 → Discord-Rolle (aktives Profil).", "Rank 0–9 → Discord role (active profile)."),
    )
    async def wga_rank_mapping_list(self, ctx):
        prof = await self._wga_active_profile(ctx)
        titles_by = await self.config.guild(ctx.guild).rank_titles_by_profile()
        mapping_by = await self.config.guild(ctx.guild).rank_mapping_by_profile()
        titles = titles_by.get(prof, {}) if isinstance(titles_by, dict) else {}
        mapping = mapping_by.get(prof, {}) if isinstance(mapping_by, dict) else {}
        if not isinstance(titles, dict):
            titles = {}
        if not isinstance(mapping, dict):
            mapping = {}
        rows = []
        for i in range(10):
            idx = str(i)
            title = str(titles.get(idx, f"Rank {idx}"))
            role_name = "—"
            try:
                role_id = int(mapping.get(title, 0) or 0)
                if role_id:
                    role = ctx.guild.get_role(role_id)
                    if role is not None:
                        role_name = role.name
            except Exception:
                role_name = "—"
            rows.append({"id": idx, "cells": {"index": idx, "title": title, "role": role_name}})
        return rows

    @wga_rank_mapping_list.edit_form
    async def _wga_rank_mapping_edit_form(self, ctx, item_id):
        prof = await self._wga_active_profile(ctx)
        idx = str(item_id)
        titles_by = await self.config.guild(ctx.guild).rank_titles_by_profile()
        mapping_by = await self.config.guild(ctx.guild).rank_mapping_by_profile()
        titles = titles_by.get(prof, {}) if isinstance(titles_by, dict) else {}
        mapping = mapping_by.get(prof, {}) if isinstance(mapping_by, dict) else {}
        if not isinstance(titles, dict):
            titles = {}
        if not isinstance(mapping, dict):
            mapping = {}
        title = str(titles.get(idx, f"Rank {idx}"))
        cur_role_id = 0
        try:
            cur_role_id = int(mapping.get(title, 0) or 0)
        except Exception:
            cur_role_id = 0
        return PanelSchema(
            description=tr(ctx, f"Rang {idx} (Profil {prof}) bearbeiten.", f"Edit rank {idx} (profile {prof})."),
            fields=[
                Field.text("title", "Title", value=title),
                Field.select(
                    "role", "Discord role", self._wga_role_options(ctx),
                    value=str(cur_role_id or ""),
                ),
            ],
        )

    @wga_rank_mapping_list.on_edit
    async def _wga_rank_mapping_edit(self, ctx, item_id, data):
        prof = await self._wga_active_profile(ctx)
        idx = str(item_id)
        new_title = str(data.get("title", "")).strip() or f"Rank {idx}"
        role_id = int(data["role"]) if str(data.get("role", "")).strip() else 0
        async with self.config.guild(ctx.guild).rank_titles_by_profile() as titles_by:
            if not isinstance(titles_by, dict):
                titles_by.clear()
            prof_titles = titles_by.get(prof)
            if not isinstance(prof_titles, dict):
                prof_titles = {}
            old_title = str(prof_titles.get(idx, f"Rank {idx}"))
            prof_titles[idx] = new_title
            titles_by[prof] = prof_titles
        async with self.config.guild(ctx.guild).rank_mapping_by_profile() as mapping_by:
            if not isinstance(mapping_by, dict):
                mapping_by.clear()
            prof_map = mapping_by.get(prof)
            if not isinstance(prof_map, dict):
                prof_map = {}
            # Remove the stale mapping of the old title if the title changed.
            if old_title != new_title and old_title in prof_map:
                prof_map.pop(old_title, None)
            if role_id:
                prof_map[new_title] = role_id
            else:
                prof_map.pop(new_title, None)
            mapping_by[prof] = prof_map
        return SubmitResult.ok("Rank-Mapping gespeichert.")

    @wga_rank_mapping_list.on_delete
    async def _wga_rank_mapping_delete(self, ctx, item_id):
        prof = await self._wga_active_profile(ctx)
        idx = str(item_id)
        old_title = f"Rank {idx}"
        async with self.config.guild(ctx.guild).rank_titles_by_profile() as titles_by:
            if isinstance(titles_by, dict):
                prof_titles = titles_by.get(prof)
                if isinstance(prof_titles, dict):
                    old_title = str(prof_titles.pop(idx, old_title))
                    titles_by[prof] = prof_titles
        async with self.config.guild(ctx.guild).rank_mapping_by_profile() as mapping_by:
            if isinstance(mapping_by, dict):
                prof_map = mapping_by.get(prof)
                if isinstance(prof_map, dict):
                    prof_map.pop(old_title, None)
                    mapping_by[prof] = prof_map
        return SubmitResult.ok("Rank-Eintrag zurückgesetzt.")

    # --- Guild panel: protected & rank-lock ------------------------------ #
    @dashboard_panel(
        "wga_protected_lock", L("Protected & Rank-Lock", "Protected & rank lock"), mount="guild_settings", permission="guild_admin",
    )
    async def wga_protected_lock_panel(self, ctx):
        prof = await self._wga_active_profile(ctx)
        protected_by = await self.config.guild(ctx.guild).protected_rank_titles_by_profile()
        locked_by = await self.config.guild(ctx.guild).locked_rank_titles_by_profile()
        protected = protected_by.get(prof, []) if isinstance(protected_by, dict) else []
        locked = locked_by.get(prof, []) if isinstance(locked_by, dict) else []
        if not isinstance(protected, list):
            protected = []
        if not isinstance(locked, list):
            locked = []
        return PanelSchema(
            description=tr(ctx, f"Geschützte und gesperrte Ränge (aktives Profil {prof}). Ein Eintrag pro Zeile.", f"Protected and locked ranks (active profile {prof}). One entry per line."),
            fields=[
                Field.textarea(
                    "protected_list", "Protected ranks",
                    value="\n".join(str(x) for x in protected),
                ),
                Field.textarea(
                    "lock_list", "Locked ranks (rank lock)",
                    value="\n".join(str(x) for x in locked),
                ),
            ],
        )

    @wga_protected_lock_panel.on_submit
    async def _wga_protected_lock_submit(self, ctx, data):
        prof = await self._wga_active_profile(ctx)
        protected = [s.strip() for s in str(data.get("protected_list", "")).splitlines() if s.strip()]
        locked = [s.strip() for s in str(data.get("lock_list", "")).splitlines() if s.strip()]
        async with self.config.guild(ctx.guild).protected_rank_titles_by_profile() as pb:
            if not isinstance(pb, dict):
                pb.clear()
            pb[prof] = protected
        async with self.config.guild(ctx.guild).locked_rank_titles_by_profile() as lb:
            if not isinstance(lb, dict):
                lb.clear()
            lb[prof] = locked
        return SubmitResult.ok("Geschützte & gesperrte Ränge gespeichert.")

    # --- Guild list: registrations --------------------------------------- #
    @dashboard_list(
        "wga_registrations", L("Registrierungen", "Registrations"), mount="guild_settings", permission="guild_admin",
        columns=[{"key": "member", "label": "Member"}],
        description=L("Gespeicherte Onboarding-Registrierungen. Löschen entfernt die Registrierung eines Mitglieds.", "Stored onboarding registrations. Deleting removes a member's registration."),
    )
    async def wga_registrations_list(self, ctx):
        all_members = await self.config.all_members(ctx.guild)
        rows = []
        for mid, mdata in (all_members or {}).items():
            reg = (mdata or {}).get("registration")
            if not reg:
                continue
            name = str(mid)
            try:
                member = ctx.guild.get_member(int(mid))
                if member is not None:
                    name = member.display_name
            except Exception:
                name = str(mid)
            rows.append({"id": str(mid), "cells": {"member": name}})
        return rows

    @wga_registrations_list.on_delete
    async def _wga_registrations_delete(self, ctx, item_id):
        try:
            member_id = int(item_id)
        except Exception:
            return SubmitResult.fail("Ungültige Mitglieds-ID.")
        try:
            await self.config.member_from_ids(ctx.guild.id, member_id).registration.clear()
        except Exception:
            return SubmitResult.fail("Registrierung konnte nicht entfernt werden.")
        return SubmitResult.ok("Registrierung entfernt.")

    async def _guild_config(self, guild: discord.Guild) -> Dict[str, Any]:
        cfg = await self.config.guild(guild).all()
        wow_profiles = cfg.get("wow_profiles", {})
        if not wow_profiles:
            wow_single = cfg.get("wow", {})
            version_key = wow_single.get("version", "retail") or "retail"
            wow_profiles = {version_key: wow_single}
            cfg["wow_profiles"] = wow_profiles
            await self.config.guild(guild).set(cfg)
        return cfg

    async def _lang(self, ctx: commands.Context) -> str:
        if isinstance(ctx.author, discord.Member):
            member_lang = await self.config.member(ctx.author).onboarding_language()
            if member_lang in ("de-DE", "en-US"):
                return member_lang
        if ctx.guild:
            guild_lang = await self.config.guild(ctx.guild).language()
            if guild_lang in ("de-DE", "en-US"):
                return guild_lang
        return "en-US"

    async def _t(self, ctx: commands.Context, key: str, **kwargs: str) -> str:
        lang = await self._lang(ctx)
        template = I18N.get(lang, I18N["en-US"]).get(key, key)
        return template.format(**kwargs)

    async def _guild_lang(self, guild: discord.Guild, member: Optional[discord.Member] = None) -> str:
        if member is not None:
            ml = await self.config.member(member).onboarding_language()
            if ml in ("de-DE", "en-US"):
                return ml
        gl = await self.config.guild(guild).language()
        if gl in ("de-DE", "en-US"):
            return gl
        return "en-US"

    async def _t_from_guild(
        self, guild: discord.Guild, member: discord.Member, key: str, **kwargs: str
    ) -> str:
        lang = await self._guild_lang(guild, member)
        template = I18N.get(lang, I18N["en-US"]).get(key, key)
        return template.format(**kwargs)

    async def _wait_text(self, ctx: commands.Context, timeout: int = 180) -> Optional[str]:
        def check(message: discord.Message) -> bool:
            return message.author.id == ctx.author.id and message.channel.id == ctx.channel.id

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=timeout)
        except Exception:
            return None
        return msg.content.strip()

    async def _send_private_ack(self, ctx: commands.Context, message: str) -> None:
        interaction = getattr(ctx, "interaction", None)
        if interaction is not None:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(message, ephemeral=True)
                else:
                    await interaction.followup.send(message, ephemeral=True)
                return
            except Exception:
                pass
        try:
            await ctx.author.send(message)
        except Exception:
            await ctx.send(message)

    async def _try_add_characters_for_member(
        self,
        guild: discord.Guild,
        member: discord.Member,
        game_type: str,
        names: List[str],
    ) -> tuple:
        cfg = await self._guild_config(guild)
        lang = await self._guild_lang(guild, member)
        templates = cfg.get("templates", {})
        dup_tpl = templates.get(
            "duplicate_character_message",
            "Dieser Charakter ist bereits verknüpft ({detail})",
        )
        prof = await wow_profile_for_game(cfg, game_type)
        if not prof or not prof.get("realm") or not prof.get("guild_name"):
            return (
                tr_lang(
                    lang,
                    f"Profil für **{game_label(game_type)}** ist unvollständig (Realm/Gilde im Dashboard setzen).",
                    f"Profile for **{game_label(game_type)}** is incomplete (set realm/guild in the dashboard).",
                ),
                False,
            )
        roster = await self.blizzard.roster_character_names(
            prof.get("region", "eu"),
            prof.get("version", game_type),
            prof.get("realm", ""),
            prof.get("guild_name", ""),
        )
        roster_l = {n.lower() for n in roster}
        linked = await get_linked_list(self.config.member(member))
        to_add: List[Dict[str, str]] = []
        for raw_name in names:
            name = (raw_name or "").strip()
            if not name:
                continue
            if name.lower() not in roster_l:
                return (
                    tr_lang(
                        lang,
                        f"`{name}` ist im **{game_label(game_type)}**-Gildenroster nicht (oder API-Fehler).",
                        f"`{name}` is not in the **{game_label(game_type)}** guild roster (or API error).",
                    ),
                    False,
                )
            key = char_tuple_key(name, game_type)
            owner = await find_char_owner_guild_wide(
                self.config, guild, name, game_type, exclude_user_id=member.id
            )
            if owner is not None:
                return (
                    dup_tpl.format(
                        detail=tr_lang(
                            lang,
                            f"bereits mit <@{owner}> verknüpft",
                            f"already linked to <@{owner}>",
                        )
                    ),
                    False,
                )
            if any(char_tuple_key(e["name"], e["game_type"]) == key for e in linked):
                return (
                    dup_tpl.format(
                        detail=tr_lang(lang, "bereits bei dir verknüpft", "already linked to you")
                    ),
                    False,
                )
            if any(char_tuple_key(e["name"], e["game_type"]) == key for e in to_add):
                continue
            to_add.append({"name": name, "game_type": game_type})
        merged = linked + to_add
        await set_linked_list(self.config.member(member), merged)
        labels = ", ".join(f"{x['name']} ({game_label(x['game_type'])})" for x in to_add)
        if labels:
            return (tr_lang(lang, f"Verknüpft: {labels}", f"Linked: {labels}"), True)
        return (tr_lang(lang, "Nichts hinzugefügt.", "Nothing added."), True)

    async def _guild_has_sync_rank(self, guild: discord.Guild) -> bool:
        cfg = await self._guild_config(guild)
        return bool(cfg.get("features", {}).get("sync_rank", True))

    async def _sync_rank_for_main(
        self,
        member: discord.Member,
        guild: discord.Guild,
        profile_key: str,
        main_name: str,
    ):
        cfg = await self._guild_config(guild)
        profiles = cfg.get("wow_profiles") or {}
        if profile_key not in profiles:
            return None, "no_profile", 0
        selected_cfg = dict(cfg)
        selected_cfg["wow"] = profiles.get(profile_key, {})
        conf = self.config.member(member)
        raw = await conf.rank_sync_by_game()
        prev_rid = 0
        if isinstance(raw, dict):
            st = raw.get(profile_key)
            if isinstance(st, dict):
                try:
                    prev_rid = int(st.get("last_role_id") or 0)
                except (TypeError, ValueError):
                    prev_rid = 0
        rank_title, reason, role_id = await self.rank_sync.sync_member_rank(
            member,
            selected_cfg,
            main_name.strip(),
            profile_key=profile_key,
            previous_bot_role_id=prev_rid,
        )
        if reason == "rank_locked" and rank_title:
            await merge_rank_sync_game_state(
                conf,
                profile_key,
                last_title=str(rank_title),
            )
            raw_rs = await conf.rank_sync_by_game()
            st = (raw_rs.get(profile_key) or {}) if isinstance(raw_rs, dict) else {}
            last_n = float(st.get("rank_lock_notice_ts") or st.get("manual_lock_notice_ts") or 0)
            now = time.time()
            if (now - last_n) >= self.RANK_LOCK_NOTICE_COOLDOWN_SEC:
                await send_rank_lock_officer_notice(
                    guild,
                    cfg,
                    member,
                    profile_key,
                    main_name.strip(),
                    rank_title,
                )
                await merge_rank_sync_game_state(
                    conf,
                    profile_key,
                    rank_lock_notice_ts=now,
                )
        elif reason == "protected" and rank_title:
            await merge_rank_sync_game_state(
                conf,
                profile_key,
                last_title=str(rank_title),
            )
            await send_protected_rank_officer_notice(
                guild,
                cfg,
                member,
                profile_key,
                main_name.strip(),
                rank_title,
            )
        return rank_title, reason, role_id

    async def _schedule_rank_sync_after_main(
        self,
        guild: discord.Guild,
        member: discord.Member,
        profile_key: str,
        main_name: str,
    ) -> None:
        try:
            if not await self._guild_has_sync_rank(guild):
                return
            rank_title, reason, role_id = await self._sync_rank_for_main(
                member, guild, profile_key, main_name
            )
            if reason == "ok" and rank_title and role_id:
                await merge_rank_sync_game_state(
                    self.config.member(member),
                    profile_key,
                    last_title=str(rank_title),
                    last_role_id=int(role_id),
                )
        except Exception:
            pass

    async def _slash_admin_sync_report_for_member(
        self,
        guild: discord.Guild,
        member: discord.Member,
    ) -> str:
        lang = await self._guild_lang(guild)
        if not await self._guild_has_sync_rank(guild):
            return tr_lang(
                lang,
                "Rang-Sync ist auf diesem Server deaktiviert (`features.sync_rank`).",
                "Rank sync is disabled on this server (`features.sync_rank`).",
            )
        cfg = await self._guild_config(guild)
        wow_profiles = cfg.get("wow_profiles") or {}
        main_map = await get_main_characters(self.config.member(member))
        conf = self.config.member(member)
        jobs: List[tuple[str, str]] = []
        for g in (GAME_RETAIL, GAME_MOP):
            if g not in wow_profiles:
                continue
            m = main_map.get(g)
            if m and str(m.get("name", "")).strip():
                jobs.append((g, str(m["name"]).strip()))
        if not jobs:
            return _NO_MAIN_MARKER + tr_lang(
                lang,
                "Kein Main für ein konfiguriertes Profil bei diesem Mitglied.",
                "No main for a configured profile for this member.",
            )
        lines: List[str] = []
        for game, name in jobs:
            rank_title, reason, role_id = await self._sync_rank_for_main(member, guild, game, name)
            gl = game_label(game)
            if reason == "ok" and rank_title and role_id:
                await merge_rank_sync_game_state(
                    conf,
                    game,
                    last_title=str(rank_title),
                    last_role_id=int(role_id),
                )
                lines.append(tr_lang(
                    lang,
                    f"• **{gl}:** `{rank_title}` synchronisiert.",
                    f"• **{gl}:** `{rank_title}` synced.",
                ))
            elif reason == "protected":
                lines.append(tr_lang(
                    lang,
                    f"• **{gl}:** geschützter Rang (Hinweis ggf. im Offizierskanal).",
                    f"• **{gl}:** protected rank (notice may be in the officer channel).",
                ))
            elif reason == "rank_locked":
                lines.append(tr_lang(
                    lang,
                    f"• **{gl}:** **Rank-Lock** (dieser Ingame-Rang) — keine Discord-Rolle vom Bot.",
                    f"• **{gl}:** **rank lock** (this in-game rank) — no Discord role from the bot.",
                ))
            elif reason == "no_profile":
                lines.append(tr_lang(
                    lang,
                    f"• **{gl}:** kein Profil konfiguriert.",
                    f"• **{gl}:** no profile configured.",
                ))
            elif reason in ("not_found", "no_role"):
                lines.append(tr_lang(
                    lang,
                    f"• **{gl}:** nicht im Roster / kein Rollen-Mapping.",
                    f"• **{gl}:** not in roster / no role mapping.",
                ))
            elif reason == "no_perms":
                lines.append(tr_lang(
                    lang,
                    f"• **{gl}:** Bot darf Rollen nicht setzen.",
                    f"• **{gl}:** bot may not assign roles.",
                ))
            elif reason == "http":
                lines.append(tr_lang(
                    lang,
                    f"• **{gl}:** Discord-API-Fehler.",
                    f"• **{gl}:** Discord API error.",
                ))
            else:
                lines.append(f"• **{gl}:** {reason}")
        return tr_lang(
            lang,
            f"**{member.display_name}** — Rang-Sync:\n",
            f"**{member.display_name}** — rank sync:\n",
        ) + "\n".join(lines)

    async def _slash_admin_sync_all_members_report(self, guild: discord.Guild) -> str:
        if not await self._guild_has_sync_rank(guild):
            return tr_lang(
                await self._guild_lang(guild),
                "Rang-Sync ist deaktiviert.",
                "Rank sync is disabled.",
            )
        data = await self.config.all_members(guild)
        blocks: List[str] = []
        for uid_str, payload in data.items():
            try:
                uid = int(uid_str)
            except (TypeError, ValueError):
                continue
            mem = guild.get_member(uid)
            if mem is None:
                continue
            linked = normalize_linked_characters(payload.get("linked_characters") or payload.get("chars"))
            if not linked:
                continue
            main_map = mains_from_member_data(payload)
            has_main = any(
                main_map.get(g) and str((main_map.get(g) or {}).get("name", "")).strip()
                for g in (GAME_RETAIL, GAME_MOP)
            )
            if not has_main:
                continue
            block = await self._slash_admin_sync_report_for_member(guild, mem)
            if block.startswith(_NO_MAIN_MARKER):
                continue
            blocks.append(block)
        lang = await self._guild_lang(guild)
        if not blocks:
            return tr_lang(
                lang,
                "Keine Mitglieder mit verknüpften Chars und gesetztem Main.",
                "No members with linked characters and a set main.",
            )
        out = "\n\n".join(blocks[:15])
        if len(blocks) > 15:
            out += tr_lang(
                lang,
                f"\n\n… gekürzt: **{len(blocks) - 15}** weitere Mitglieder — erneut ausführen oder einzeln syncen.",
                f"\n\n… truncated: **{len(blocks) - 15}** more members — run again or sync individually.",
            )
        return out[:3900]

    async def _background_rank_sync_guild_members(self, guild: discord.Guild) -> None:
        data = await self.config.all_members(guild)
        for uid_str, payload in data.items():
            try:
                uid = int(uid_str)
            except (TypeError, ValueError):
                continue
            mem = guild.get_member(uid)
            if mem is None:
                continue
            linked = normalize_linked_characters(
                payload.get("linked_characters") or payload.get("chars")
            )
            if not linked:
                continue
            main_map = mains_from_member_data(payload)
            has_main = any(
                main_map.get(g) and str((main_map.get(g) or {}).get("name", "")).strip()
                for g in (GAME_RETAIL, GAME_MOP)
            )
            if not has_main:
                continue
            report = await self._slash_admin_sync_report_for_member(guild, mem)
            if report.startswith(_NO_MAIN_MARKER):
                continue

    async def _maybe_auto_rank_sync_guild(self, guild: discord.Guild) -> None:
        cfg = await self.config.guild(guild).all()
        mins = int(cfg.get("rank_sync_interval_minutes") or 0)
        if mins <= 0:
            return
        if not await self._guild_has_sync_rank(guild):
            return
        last = float(cfg.get("rank_sync_last_run_epoch") or 0)
        now = time.time()
        if last <= 0:
            cfg2 = dict(cfg)
            cfg2["rank_sync_last_run_epoch"] = now
            await self.config.guild(guild).set(cfg2)
            return
        if (now - last) < mins * 60:
            return
        await self._background_rank_sync_guild_members(guild)
        cfg3 = await self.config.guild(guild).all()
        cfg3["rank_sync_last_run_epoch"] = time.time()
        await self.config.guild(guild).set(cfg3)

    @tasks.loop(minutes=1)
    async def _rank_auto_sync_loop(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self._maybe_auto_rank_sync_guild(guild)
            except Exception:
                pass

    @_rank_auto_sync_loop.before_loop
    async def _rank_auto_sync_before_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _slash_format_rankmap_text(self, guild: discord.Guild) -> str:
        cfg = await self._guild_config(guild)
        pk = str(cfg.get("active_profile_key") or "retail")
        titles = (cfg.get("rank_titles_by_profile") or {}).get(pk) or cfg.get("rank_titles") or {}
        m = (cfg.get("rank_mapping_by_profile") or {}).get(pk) or cfg.get("rank_mapping") or {}
        lang = await self._guild_lang(guild)
        lines: List[str] = [
            tr_lang(lang, f"**Rang-Mapping** — aktives Profil `{pk}`", f"**Rank mapping** — active profile `{pk}`"),
            "",
            tr_lang(lang, "**Indizes → Titel:**", "**Indexes → titles:**"),
        ]
        any_title = False
        for i in range(10):
            t = titles.get(str(i))
            if t:
                any_title = True
                lines.append(f"• [{i}] `{t}`")
        if not any_title:
            lines.append(tr_lang(lang, "_(keine Titel gesetzt)_", "_(no titles set)_"))
        lines.extend(["", tr_lang(lang, "**Titel/Name → Rolle:**", "**Title/name → role:**")])
        if not m:
            lines.append(tr_lang(lang, "_(keine Einträge)_", "_(no entries)_"))
        else:
            for name, rid in sorted(m.items(), key=lambda x: str(x[0]).lower()):
                try:
                    rid_i = int(rid)
                except (TypeError, ValueError):
                    continue
                lines.append(f"• `{name}` → <@&{rid_i}>")
        return "\n".join(lines)

    async def _slash_format_onboardings_text(self, guild: discord.Guild) -> str:
        data = await self.config.all_members(guild)
        lines: List[str] = []
        for uid_str, payload in data.items():
            reg = payload.get("registration") or {}
            if not reg:
                continue
            try:
                uid = int(uid_str)
            except (TypeError, ValueError):
                continue
            mem = guild.get_member(uid)
            who = mem.display_name if mem else str(uid)
            lines.append(f"• **{who}** (`{uid}`): `{reg}`")
        if lines:
            return "\n".join(lines)
        return tr_lang(
            await self._guild_lang(guild),
            "Keine Registrierungsdaten bei Mitgliedern.",
            "No registration data for members.",
        )

    async def _slash_format_admin_readytimes_text(self, guild: discord.Guild) -> str:
        data = await self.config.all_members(guild)
        blocks: List[str] = []
        for uid_str, payload in data.items():
            rt = payload.get("ready_times")
            if not member_marked_any_day(rt):
                continue
            try:
                uid = int(uid_str)
            except (TypeError, ValueError):
                continue
            mem = guild.get_member(uid)
            label = mem.mention if mem else f"<@{uid}>"
            block = format_member_ready_times_block(rt)
            blocks.append(f"{label}\n{block}")
        if not blocks:
            return tr_lang(
                await self._guild_lang(guild),
                "Niemand hat aktive Bereitschaftszeiten eingetragen.",
                "Nobody has entered active ready times.",
            )
        return "\n\n".join(blocks)

    async def _format_user_char_list_ephemeral(
        self,
        guild: discord.Guild,
        member: discord.Member,
        *,
        header_user: bool = False,
    ) -> str:
        linked = await get_linked_list(self.config.member(member))
        mains = await get_main_characters(self.config.member(member))
        rs_raw = await self.config.member(member).rank_sync_by_game()
        rank_line = format_rank_sync_summary(guild, rs_raw)
        lang = await self._guild_lang(guild, member)
        last_sync_label = tr_lang(lang, "**Letzter Rang-Sync:**", "**Last rank sync:**")
        head = f"**{member.display_name}** (`{member.id}`)\n" if header_user else ""
        if not linked:
            extra = format_mains_summary(mains)
            if rank_line:
                extra += f"\n{last_sync_label} {rank_line}"
            return head + tr_lang(lang, "Keine Chars verknüpft.\n", "No characters linked.\n") + extra
        lines = [format_char_line(e, mains) for e in linked]
        block = format_mains_summary(mains)
        if rank_line:
            block += f"\n{last_sync_label} {rank_line}"
        return head + block + "\n\n" + "\n".join(lines)

    async def _officer_format_all_linked_chars(self, guild: discord.Guild) -> str:
        data = await self.config.all_members(guild)
        lines: List[str] = []
        for uid_str, payload in data.items():
            linked = normalize_linked_characters(
                payload.get("linked_characters") or payload.get("chars")
            )
            if not linked:
                continue
            try:
                uid = int(uid_str)
            except (TypeError, ValueError):
                continue
            mem = guild.get_member(uid)
            label = mem.mention if mem else f"<@{uid}>"
            mains = mains_from_member_data(payload)
            parts = [format_char_line(e, mains) for e in linked]
            rank_snip = format_rank_sync_summary(guild, payload.get("rank_sync_by_game"))
            suffix = f" | {rank_snip}" if rank_snip else ""
            lines.append(f"{label}: {', '.join(parts)}{suffix}")
        if lines:
            return "\n".join(lines)
        return tr_lang(
            await self._guild_lang(guild),
            "Keine verknüpften Chars auf diesem Server.",
            "No linked characters on this server.",
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        linked = await get_linked_list(self.config.member(member))
        mains_before = await get_main_characters(self.config.member(member))
        main_str = ", ".join(
            f"{game_label(g)}: {m['name']}"
            for g, m in mains_before.items()
            if m and str(m.get("name", "")).strip()
        )
        had_chars = bool(linked)
        await self.config.member(member).linked_characters.clear()
        await self.config.member(member).chars.clear()
        await self.config.member(member).main_character.clear()
        await self.config.member(member).main_characters.clear()
        await self.config.member(member).rank_sync_by_game.clear()
        await self.config.member(member).selected_game.clear()
        await self.config.member(member).registration.clear()
        if not had_chars:
            return
        guild = member.guild
        cfg = await self._guild_config(guild)
        ch_id = int(cfg.get("channels", {}).get("officer_character_notify_channel_id", 0) or 0)
        channel = guild.get_channel(ch_id) if ch_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        tpl = cfg.get("templates", {}).get(
            "member_left_characters_notice",
            "{user} hat den Server verlassen. Chars: {chars}",
        )
        char_lines = []
        for e in linked:
            char_lines.append(f"{e['name']} ({game_label(e['game_type'])})")
        chars_str = ", ".join(char_lines)
        try:
            await channel.send(
                tpl.format(
                    user=f"{member} ({member.id})",
                    username=str(member),
                    chars=chars_str,
                    main=main_str,
                )
            )
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_dashboard_cog_add(self, dashboard_cog: commands.Cog) -> None:
        if self._dashboard_attached:
            return
        self._dashboard_attached = self._attach_to_dashboard(dashboard_cog)

    @commands.Cog.listener()
    async def on_dashboard_cog_remove(self, dashboard_cog: commands.Cog) -> None:
        _ = dashboard_cog
        self._dashboard_attached = False

    async def _apply_onboarding_channel_permissions(self, guild: discord.Guild) -> None:
        cfg = await self._guild_config(guild)
        channel_id = cfg.get("channels", {}).get("onboarding_channel_id", 0)
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        roles = cfg.get("roles", {})
        new_role = guild.get_role(roles.get("onboarding_new_role_id", 0))
        complete_role = guild.get_role(roles.get("onboarding_complete_role_id", 0))

        await channel.set_permissions(guild.default_role, view_channel=False, send_messages=False)
        if new_role:
            await channel.set_permissions(new_role, view_channel=True, send_messages=False)
        if complete_role:
            await channel.set_permissions(complete_role, view_channel=False, send_messages=False)

    async def _run_onboarding_flow(self, member: discord.Member, simulated: bool = False) -> None:
        guild_cfg = await self._guild_config(member.guild)
        if not guild_cfg.get("features", {}).get("onboarding", True):
            return
        session_id = f"{datetime.now(timezone.utc).timestamp()}:{member.guild.id}:{member.id}"
        await self.config.member(member).onboarding_session_id.set(session_id)

        new_role_id = guild_cfg.get("roles", {}).get("onboarding_new_role_id", 0)
        if new_role_id:
            new_role = member.guild.get_role(new_role_id)
            if new_role and new_role not in member.roles:
                await member.add_roles(new_role, reason="Onboarding started")

        manual_channel_id = guild_cfg.get("channels", {}).get("manual_review_channel_id", 0)
        manual_channel = member.guild.get_channel(manual_channel_id) if manual_channel_id else None
        if manual_channel and not isinstance(manual_channel, discord.TextChannel):
            manual_channel = None
        onboarding_channel_id = guild_cfg.get("channels", {}).get("onboarding_channel_id", 0)
        onboarding_channel = (
            member.guild.get_channel(onboarding_channel_id) if onboarding_channel_id else None
        )
        if onboarding_channel and not isinstance(onboarding_channel, discord.TextChannel):
            onboarding_channel = None

        onboarding_result = await handle_new_member_onboarding(
            bot=self.bot,
            member=member,
            guild_config=guild_cfg,
            rank_sync=self.rank_sync,
            manual_channel=manual_channel,  # type: ignore[arg-type]
            onboarding_channel=onboarding_channel,  # type: ignore[arg-type]
            member_config=self.config.member(member),
        )
        chosen_lang = onboarding_result.get("language", "en-US")
        selected_game = onboarding_result.get("selected_game", "retail")
        registration = onboarding_result.get("registration", {})
        registration["registered_at"] = datetime.now(timezone.utc).isoformat()
        rules_confirmed = bool(registration.get("rules_confirmed", False))
        await self.config.member(member).onboarding_language.set(chosen_lang)
        await self.config.member(member).selected_game.set(selected_game)
        await self.config.member(member).registration.set(registration)

        char_from_onboarding = str(registration.get("char_name") or "").strip()
        if registration.get("type") == "member" and char_from_onboarding:
            link_game = profile_key_to_link_game(selected_game)
            merged_ok = await merge_onboarding_character_into_linked(
                self.config,
                member.guild,
                member,
                char_from_onboarding,
                selected_game,
            )
            if merged_ok:
                await set_main_for_game(
                    self.config.member(member),
                    link_game,
                    char_from_onboarding,
                )

        complete_role_id = guild_cfg.get("roles", {}).get("onboarding_complete_role_id", 0)
        if complete_role_id and rules_confirmed:
            complete_role = member.guild.get_role(complete_role_id)
            if complete_role and complete_role not in member.roles:
                await member.add_roles(complete_role, reason="Onboarding completed")

        if new_role_id and rules_confirmed:
            new_role = member.guild.get_role(new_role_id)
            if new_role and new_role in member.roles:
                await member.remove_roles(new_role, reason="Onboarding completed")

        if not rules_confirmed and not simulated:
            asyncio.create_task(self._send_rules_reminder_later(member, session_id))

    async def _send_rules_reminder_later(
        self, member: discord.Member, session_id: str, delay_seconds: int = 1800
    ) -> None:
        await asyncio.sleep(delay_seconds)
        # Only remind for the same fresh onboarding session.
        current_session = await self.config.member(member).onboarding_session_id()
        if current_session != session_id:
            return
        registration = await self.config.member(member).registration()
        if registration.get("rules_confirmed", False):
            return
        try:
            rlang = await self._guild_lang(member.guild, member)
            dm = await member.create_dm()
            await dm.send(tr_lang(rlang,
                "Erinnerung: Bitte bestätige noch die Serverregeln— im Regel-Kanal mit dem konfigurierten Emoji "
                "(z. B. ✅) auf die **bestehende** Regelnachricht, damit dein Onboarding abgeschlossen wird.",
                "Reminder: Please confirm the server rules - in the rules channel with the configured emoji "
                "on the existing rules message so your onboarding can be completed.",
            ))
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._run_onboarding_flow(member, simulated=False)


    @app_commands.command(
        name="wow-user",
        description="WoW: panel, characters, rank sync, ready times.",
        extras={"i18n_desc": {
            "de-DE": "WoW: Panel, Charaktere, Rang-Sync, Bereitschaftszeiten.",
            "en-US": "WoW: panel, characters, rank sync, ready times.",
        }},
    )
    @app_commands.guild_only()
    @app_commands.describe(action="Action")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Panel (interactive menu)", value="panel"),
            app_commands.Choice(name="My characters (list)", value="list_my"),
            app_commands.Choice(name="Rank sync (my mains)", value="sync_my_profile"),
            app_commands.Choice(name="Ready times (only you)", value="readytimes"),
        ]
    )
    async def slash_wow_user(self, interaction: discord.Interaction, action: str) -> None:
        """User commands for WoW guild onboarding and verification."""
        if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
            await interaction.response.send_message(
                tr_lang("en-US", "Nur auf einem Server.", "Only on a server."), ephemeral=True
            )
            return
        lang = await self._guild_lang(interaction.guild, interaction.user)
        if action == "panel":
            await interaction.response.send_message(
                _panel_intro(lang),
                ephemeral=True,
                view=CharMainMenuView(self, interaction.guild, interaction.user, lang=lang),
            )
            return
        if action == "list_my":
            await interaction.response.defer(ephemeral=True)
            text = await self._format_user_char_list_ephemeral(
                interaction.guild,
                interaction.user,
                header_user=False,
            )
            chars_none = await self._t_from_guild(interaction.guild, interaction.user, "chars_none")
            display = text if text else chars_none
            for chunk in [display[i : i + 1900] for i in range(0, len(display), 1900)] or ["—"]:
                await interaction.followup.send(chunk, ephemeral=True)
            return
        if action == "sync_my_profile":
            await interaction.response.defer(ephemeral=True)
            report = await self._slash_admin_sync_report_for_member(interaction.guild, interaction.user)
            await interaction.followup.send(report[:1900], ephemeral=True)
            return
        if action == "readytimes":
            await send_member_readytimes_panel(self, interaction, interaction.user)
            return
        await interaction.response.send_message(
            tr_lang(lang, "Unbekannte Aktion.", "Unknown action."), ephemeral=True
        )

    @app_commands.command(
        name="wow-admin",
        description="Officer: character panel, rank sync, guild info, onboarding data (Manage Server).",
        extras={"i18n_desc": {
            "de-DE": "Offizier: Charakter-Panel, Rang-Sync, Gildeninfo, Onboarding-Daten (Server verwalten).",
            "en-US": "Officer: character panel, rank sync, guild info, onboarding data (Manage Server).",
        }},
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(action="Action")
    @app_commands.choices(
        action=[
            app_commands.Choice(
                name="Panel (Mitglied wählen + Buttons)",
                value="panel",
            ),
            app_commands.Choice(name="Rank sync: one member", value="sync_specific_member"),
            app_commands.Choice(name="Rank sync: all with a main", value="sync_all_members"),
            app_commands.Choice(name="Simulate onboarding (join)", value="simulate_join"),
            app_commands.Choice(name="Guild profile (modal)", value="guildsettings"),
            app_commands.Choice(name="Delete registration (pick user)", value="remove_registration"),
            app_commands.Choice(name="List registrations", value="list_onboardings"),
            app_commands.Choice(name="List rank mapping", value="list_rankmap"),
            app_commands.Choice(name="Ready times (overview)", value="readytimes"),
        ]
    )
    async def slash_wow_admin(self, interaction: discord.Interaction, action: str) -> None:
        """Admin commands for WoW guild automation."""
        if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
            await interaction.response.send_message(
                tr_lang("en-US", "Nur auf einem Server.", "Only on a server."), ephemeral=True
            )
            return
        lang = await self._guild_lang(interaction.guild, interaction.user)
        if not officer_can_manage_characters(interaction.user):
            await interaction.response.send_message(
                tr_lang(
                    lang,
                    "Nur für Mitglieder mit **Server verwalten** (oder Administrator).",
                    "Only for members with **Manage Server** (or Administrator).",
                ),
                ephemeral=True,
            )
            return
        guild = interaction.guild
        if action == "panel":
            await interaction.response.send_message(
                _admin_panel_intro(lang),
                ephemeral=True,
                view=WowAdminCharPanelView(self, guild, interaction.user, lang=lang),
            )
            return
        if action == "sync_specific_member":
            await interaction.response.send_message(
                tr_lang(lang, "Welches Mitglied synchronisieren?", "Which member to sync?"),
                ephemeral=True,
                view=AdminPickOneMemberView(self, guild, interaction.user, mode="sync_rank_member", lang=lang),
            )
            return
        if action == "sync_all_members":
            await interaction.response.send_message(
                tr_lang(
                    lang,
                    "Alle Mitglieder mit verknüpften Chars **und** gesetztem Main werden nacheinander synchronisiert "
                    "(kann bei großen Gilden lange dauern). Bitte bestätigen:",
                    "All members with linked characters **and** a set main will be synced one after another "
                    "(can take a while for large guilds). Please confirm:",
                ),
                ephemeral=True,
                view=SlashWowAdminSyncAllConfirmView(self, guild, interaction.user),
            )
            return
        if action == "simulate_join":
            await interaction.response.send_message(
                tr_lang(
                    lang,
                    "Welches Mitglied? Der Bot führt das Onboarding **simuliert** aus.",
                    "Which member? The bot runs the onboarding in **simulation** mode.",
                ),
                ephemeral=True,
                view=AdminPickOneMemberView(self, guild, interaction.user, mode="simulate_join", lang=lang),
            )
            return
        if action == "guildsettings":
            await interaction.response.send_modal(GuildSettingsModal(self, guild, lang))
            return
        if action == "remove_registration":
            await interaction.response.send_message(
                tr_lang(
                    lang,
                    "Welche Registrierung soll gelöscht werden?",
                    "Which registration should be deleted?",
                ),
                ephemeral=True,
                view=AdminPickOneMemberView(self, guild, interaction.user, mode="remove_registration", lang=lang),
            )
            return
        if action == "list_onboardings":
            await interaction.response.defer(ephemeral=True)
            text = await self._slash_format_onboardings_text(guild)
            for chunk in [text[i : i + 1900] for i in range(0, len(text), 1900)] or ["—"]:
                await interaction.followup.send(chunk, ephemeral=True)
            return
        if action == "list_rankmap":
            await interaction.response.defer(ephemeral=True)
            text = await self._slash_format_rankmap_text(guild)
            for chunk in [text[i : i + 1900] for i in range(0, len(text), 1900)] or ["—"]:
                await interaction.followup.send(chunk, ephemeral=True)
            return
        if action == "readytimes":
            await interaction.response.defer(ephemeral=True)
            text = await self._slash_format_admin_readytimes_text(guild)
            for chunk in [text[i : i + 1900] for i in range(0, len(text), 1900)] or ["—"]:
                await interaction.followup.send(chunk, ephemeral=True)
            return
        await interaction.response.send_message(
            tr_lang(lang, "Unbekannte Aktion.", "Unknown action."), ephemeral=True
        )

    @app_commands.command(
        name="wow-masteradmin",
        description="Advanced: onboarding, Blizzard API, rank mapping, auto sync, per-member rank lock.",
        extras={"i18n_desc": {
            "de-DE": "Erweitert: Onboarding, Blizzard-API, Rang-Mapping, Auto-Sync, Rang-Sperre pro Mitglied.",
            "en-US": "Advanced: onboarding, Blizzard API, rank mapping, auto sync, per-member rank lock.",
        }},
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(action="Action")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Onboarding channel & roles (modal)", value="onboarding_setup"),
            app_commands.Choice(name="Blizzard API (bot owner only)", value="botsetup"),
            app_commands.Choice(name="Set rank title (index 0–9)", value="setranktitle"),
            app_commands.Choice(name="Map rank → role (modal)", value="maprank"),
            app_commands.Choice(name="Global bot defaults (bot owner only)", value="mastersetup_bot"),
            app_commands.Choice(name="Auto rank-sync interval (minutes)", value="syncsetup"),
            app_commands.Choice(name="Rank lock: lock in-game rank (modal)", value="rank_lock"),
            app_commands.Choice(name="Rank lock: remove entry (modal)", value="rank_unlock"),
        ]
    )
    async def slash_wow_masteradmin(self, interaction: discord.Interaction, action: str) -> None:
        """Master-admin setup commands for WoW guild automation."""
        if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
            await interaction.response.send_message(
                tr_lang("en-US", "Nur auf einem Server.", "Only on a server."), ephemeral=True
            )
            return
        lang = await self._guild_lang(interaction.guild, interaction.user)
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                tr_lang(
                    lang,
                    "Fehlende Berechtigung **Server verwalten**.",
                    "Missing permission **Manage Server**.",
                ),
                ephemeral=True,
            )
            return
        guild = interaction.guild
        if action in ("botsetup", "mastersetup_bot"):
            if interaction.user.id not in self.bot.owner_ids:
                await interaction.response.send_message(
                    tr_lang(lang, "Nur der **Bot-Besitzer** kann das.", "Only the **bot owner** can do this."),
                    ephemeral=True,
                )
                return
        if action == "onboarding_setup":
            await interaction.response.send_modal(OnboardingSetupModal(self, guild, lang))
            return
        if action == "botsetup":
            await interaction.response.send_modal(BotSetupModal(self, lang))
            return
        if action == "setranktitle":
            await interaction.response.send_modal(SetRankTitleModal(self, guild, lang))
            return
        if action == "maprank":
            await interaction.response.send_modal(MapRankModal(self, guild, lang))
            return
        if action == "mastersetup_bot":
            await interaction.response.send_modal(MasterSetupModal(self, lang))
            return
        if action == "syncsetup":
            await interaction.response.send_modal(SyncIntervalModal(self, guild, lang))
            return
        if action == "rank_lock":
            await interaction.response.send_modal(RankLockAddModal(self, guild, lang))
            return
        if action == "rank_unlock":
            await interaction.response.send_modal(RankLockRemoveModal(self, guild, lang))
            return
        await interaction.response.send_message(
            tr_lang(lang, "Unbekannte Aktion.", "Unknown action."), ephemeral=True
        )

    @_dashboard_page(name=None, description="WoW Guild Automation Dashboard")
    async def dashboard_home(self, **kwargs: Any) -> Dict[str, Any]:
        _ = kwargs
        source = """
<div style="padding: 12px;">
  <h2>WoW Guild Automation</h2>
  <p>Dashboard integration is active.</p>
  <p>Use contextual pages:</p>
  <ul>
    <li><b>wowguild_master</b> (bot owner/global settings)</li>
    <li><b>wowguild_automation</b> (guild/server settings)</li>
  </ul>
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
        name="wowguild_master",
        description="Global bot master settings for WoW Guild Automation.",
        methods=("GET", "POST"),
        context_ids=["user_id"],
        is_owner=True,
        hidden=False,
    )
    async def dashboard_wowguild_master(
        self,
        user_id: Optional[int] = None,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        try:
            _ = kwargs
            if user_id is None:
                return {
                    "status": 0,
                    "error_code": 400,
                    "message": "Missing context: user_id. Open this page from a logged-in owner context.",
                }
            if user_id not in self.bot.owner_ids:
                return {"status": 1, "message": "Not allowed."}

            bot_setup = await self.config.bot_setup()
            Form = kwargs.get("Form")
            if Form is not None:
                import wtforms

                class MasterForm(Form):
                    def __init__(_self) -> None:
                        super().__init__(prefix="master_")

                    client_id = wtforms.StringField("Blizzard Client ID")
                    client_secret = wtforms.StringField("Blizzard Client Secret")
                    default_language = wtforms.SelectField(
                        "Default Language",
                        choices=[("de-DE", "de-DE"), ("en-US", "en-US")],
                        validators=[wtforms.validators.DataRequired()],
                    )
                    default_region = wtforms.StringField(
                        "Default Region", validators=[wtforms.validators.DataRequired()]
                    )
                    default_version = wtforms.StringField(
                        "Default Version", validators=[wtforms.validators.DataRequired()]
                    )
                    dashboard_enabled = wtforms.BooleanField("Dashboard Enabled")
                    submit = wtforms.SubmitField("Save Master Settings")

                form = MasterForm()
                if method.upper() == "GET":
                    form.client_id.data = bot_setup.get("client_id", "")
                    form.client_secret.data = bot_setup.get("client_secret", "")
                    form.default_language.data = bot_setup.get("default_language", "de-DE")
                    form.default_region.data = bot_setup.get("default_region", "eu")
                    form.default_version.data = bot_setup.get("default_version", "retail")
                    form.dashboard_enabled.data = bool(bot_setup.get("dashboard_enabled", True))

                if form.validate_on_submit():
                    lang = str(form.default_language.data).strip()
                    if lang not in ("de-DE", "en-US"):
                        lang = "de-DE"
                    bot_setup["client_id"] = str(form.client_id.data or "").strip()
                    bot_setup["client_secret"] = str(form.client_secret.data or "").strip()
                    bot_setup["default_language"] = lang
                    bot_setup["default_region"] = str(form.default_region.data).strip().lower()
                    bot_setup["default_version"] = str(form.default_version.data).strip().lower()
                    bot_setup["dashboard_enabled"] = bool(form.dashboard_enabled.data)
                    await self.config.bot_setup.set(bot_setup)
                    self.blizzard.client_id = bot_setup["client_id"]
                    self.blizzard.client_secret = bot_setup["client_secret"]
                    return {
                        "status": 0,
                        "notifications": [{"message": "WoW master settings saved.", "category": "success"}],
                        "redirect_url": kwargs.get("request_url"),
                    }

                source = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
.wow-wrap {{
  font-family: 'Inter', sans-serif;
  background: rgba(18, 23, 33, 0.6);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  padding: 24px;
  color: #f3e9d2;
  box-shadow: 0 8px 32px 0 rgba(0,0,0,.3);
}}
.wow-wrap h2, .wow-wrap h3 {{ color: #ffffff; margin: 4px 0 16px 0; font-weight: 600; letter-spacing: -0.02em; }}
.wow-wrap p {{ margin-top: 0; margin-bottom: 14px; line-height: 1.5; color: #a0aec0; }}
.wow-wrap label {{ color: #cbd5e0; font-weight: 500; font-size: 13.5px; margin-bottom: 6px; display: inline-block; }}
.wow-wrap input, .wow-wrap select {{
  background: rgba(0, 0, 0, 0.25);
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  padding: 10px 14px;
  min-width: 360px;
  font-size: 14px;
  transition: all 0.2s ease;
  box-sizing: border-box;
}}
.wow-wrap input:focus, .wow-wrap select:focus {{
  outline: none;
  border-color: #4299e1;
  box-shadow: 0 0 0 3px rgba(66, 153, 225, 0.25);
  background: rgba(0, 0, 0, 0.35);
}}
</style>
<div class="wow-wrap">
  <h2>WoW Master Settings</h2>
  <p>Global defaults and Blizzard credentials for all guild instances.</p>
  <form method="post">
    {form.hidden_tag()}
    <p><label>Default Language</label><br>{form.default_language()}</p>
    <p><label>Blizzard Client ID</label><br>{form.client_id()}</p>
    <p><label>Blizzard Client Secret</label><br>{form.client_secret()}</p>
    <p><label>Default Region</label><br>{form.default_region()}</p>
    <p><label>Default Version</label><br>{form.default_version()}</p>
    <p><label>{form.dashboard_enabled()} Dashboard Enabled</label></p>
    <p>{form.submit()}</p>
  </form>
</div>
"""
                return {"status": 0, "web_content": {"source": source, "standalone": True}}

            return {
                "status": 0,
                "web_content": {
                    "source": (
                        "<div style='padding:12px;'>"
                        "<h2>WoW Master Settings</h2>"
                        "<p>Use POST on this page endpoint to update values.</p>"
                        "<h3>Current Config</h3>"
                        f"<pre>{json.dumps(bot_setup, indent=2)}</pre>"
                        "<h3>Payload Example</h3>"
                        "<pre>{\n"
                        '  "default_language": "de-DE",\n'
                        '  "default_region": "eu",\n'
                        '  "default_version": "retail",\n'
                        '  "dashboard_enabled": true\n'
                        "}</pre>"
                        "</div>"
                    ),
                    "standalone": True,
                },
            }
        except Exception as e:
            return {
                "status": 0,
                "error_code": 500,
                "message": f"Master page failed: {e}",
                "error_message": traceback.format_exc(limit=2),
            }

    @_dashboard_page(
        name="wowguild_automation",
        description="Configure WoW Guild Automation for this server.",
        methods=("GET", "POST"),
        context_ids=["user_id", "guild_id"],
        hidden=False,
    )
    async def dashboard_wowguild_automation(
        self,
        user_id: Optional[int] = None,
        guild_id: Optional[int] = None,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        try:
            _ = kwargs
            if user_id is None or guild_id is None:
                return {
                    "status": 0,
                    "error_code": 400,
                    "message": "Missing context: user_id/guild_id. Open this page from a server context.",
                }
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return {"status": 1, "message": "Guild not found."}
            member = guild.get_member(user_id)
            if user_id not in self.bot.owner_ids and (
                member is None or not (await self.bot.is_admin(member) or member.guild_permissions.manage_guild)
            ):
                return {"status": 1, "message": "Not allowed."}

            cfg = await self._guild_config(guild)
            Form = kwargs.get("Form")
            if Form is not None:
                import wtforms

                class GuildForm(Form):
                    def __init__(_self) -> None:
                        super().__init__(prefix="guild_")

                    language = wtforms.SelectField(
                        "Language", choices=[("de-DE", "de-DE"), ("en-US", "en-US")]
                    )
                    profile_key = wtforms.SelectField("WoW Profile")
                    new_profile_version = wtforms.SelectField("Create New Profile For Version")
                    region = wtforms.StringField("Profile Region")
                    version = wtforms.SelectField(
                        "Profile Version",
                        choices=[
                            ("retail", "retail"),
                            ("classic", "classic"),
                            ("classic_era", "classic_era"),
                            ("mop_classic", "mop_classic"),
                            ("sod", "sod"),
                        ],
                    )
                    realm = wtforms.StringField("Realm")
                    guild_name = wtforms.StringField("Guild Name")
                    welcome_text_de = wtforms.StringField("Onboarding Text DE")
                    welcome_text_en = wtforms.StringField("Onboarding Text EN")
                    guest_role_id = wtforms.SelectField("Guest Role")
                    create_guest_role = wtforms.BooleanField("Create Guest Role if missing")
                    allied_role_id = wtforms.SelectField("Allied Role")
                    allied_guilds_enabled = wtforms.BooleanField("Allied Guilds Enabled")
                    allied_guilds = wtforms.TextAreaField("Allied Guilds (one per line)")
                    member_role_id = wtforms.SelectField("Member Role")
                    create_member_role = wtforms.BooleanField("Create Member Role if missing")
                    onboarding_new_role_id = wtforms.SelectField("Onboarding New Role")
                    create_onboarding_new_role = wtforms.BooleanField(
                        "Create Onboarding New Role if missing"
                    )
                    onboarding_complete_role_id = wtforms.SelectField("Onboarding Complete Role")
                    create_onboarding_complete_role = wtforms.BooleanField(
                        "Create Onboarding Complete Role if missing"
                    )
                    onboarding_channel_id = wtforms.SelectField("Onboarding Channel")
                    create_onboarding_channel = wtforms.BooleanField(
                        "Create Onboarding Channel if missing"
                    )
                    manual_review_channel_id = wtforms.SelectField("Manual Review Channel")
                    create_manual_review_channel = wtforms.BooleanField(
                        "Create Manual Review Channel if missing"
                    )
                    raid_guest_channel_id = wtforms.SelectField("Raid Guest Channel")
                    create_raid_guest_channel = wtforms.BooleanField(
                        "Create Raid Guest Channel if missing"
                    )
                    rule_channel_id = wtforms.SelectField("Rules Channel")
                    rule_emoji = wtforms.StringField("Rules Confirmation Emoji")
                    target_category_id = wtforms.SelectField("Target Category for new channels")
                    guest_role_name = wtforms.StringField("Create Guest Role Name")
                    member_role_name = wtforms.StringField("Create Member Role Name")
                    onboarding_new_role_name = wtforms.StringField("Create Onboarding New Role Name")
                    onboarding_complete_role_name = wtforms.StringField("Create Onboarding Complete Role Name")
                    onboarding_channel_name = wtforms.StringField("Create Onboarding Channel Name")
                    manual_review_channel_name = wtforms.StringField("Create Manual Review Channel Name")
                    raid_guest_channel_name = wtforms.StringField("Create Raid Guest Channel Name")
                    map_rank_index = wtforms.SelectField("Guild Rank Index (0-9)")
                    map_rank_title = wtforms.StringField("Rank Title (optional)")
                    map_role_id = wtforms.SelectField("Discord Role for this rank")
                    remove_rank_index = wtforms.SelectField("Remove mapping by rank index")
                    remove_registration_user_id = wtforms.SelectField("Remove registration entry")
                    confirm_remove_registration = wtforms.BooleanField(
                        "I understand this permanently removes the registration entry"
                    )
                    load_profile = wtforms.SubmitField("Load Selected Profile")
                    apply_rank_mapping = wtforms.SubmitField("Apply Rank Mapping")
                    remove_rank_mapping = wtforms.SubmitField("Remove Rank Mapping")
                    remove_registration = wtforms.SubmitField("Remove Registration Entry")
                    officer_character_notify_channel_id = wtforms.SelectField(
                        "Officer channel (member left + linked chars notice)"
                    )
                    duplicate_character_message = wtforms.TextAreaField(
                        "Message: character already linked / invalid"
                    )
                    member_left_characters_notice = wtforms.TextAreaField(
                        "Message: member left (vars: {user}, {username}, {chars})"
                    )
                    admin_removed_char_dm = wtforms.TextAreaField(
                        "DM text after officer removed chars (vars: {chars}, {reason}, {officer})"
                    )
                    rank_protected_notify_channel_id = wtforms.SelectField(
                        "Channel: Hinweise bei Protected-Rängen (kein Auto-Discord-Rang)"
                    )
                    protected_rank_lines = wtforms.TextAreaField(
                        "Protected: Ingame-Ränge (eine Zeile: Titel, API-Name oder Index 0–9)"
                    )
                    locked_rank_lines = wtforms.TextAreaField(
                        "Rank-Lock: Ingame-Ränge ohne Bot-Discord-Rolle (eine Zeile wie bei Protected)"
                    )
                    protected_rank_sync_notice = wtforms.TextAreaField(
                        "Vorlage Offiziers-Hinweis Protected ({member}, …)"
                    )
                    rank_lock_notify_channel_id = wtforms.SelectField(
                        "Kanal Rank-Lock-Hinweise (0 = gleicher Kanal wie Protected)"
                    )
                    rank_lock_officer_notice = wtforms.TextAreaField(
                        "Vorlage Offiziers-Hinweis Rank-Lock (gleiche Platzhalter wie Protected)"
                    )
                    save_protected_ranks = wtforms.SubmitField("Protected- & Rank-Lock-Listen speichern")
                    submit = wtforms.SubmitField("Save Guild Settings")

                form = GuildForm()
                wow_profiles = cfg.get("wow_profiles", {})
                active_key = cfg.get("active_profile_key", "") or next(iter(wow_profiles.keys()), "retail")
                if active_key not in wow_profiles and wow_profiles:
                    active_key = next(iter(wow_profiles.keys()))
                wow = wow_profiles.get(active_key, cfg.get("wow", {}))
                roles = cfg.get("roles", {})
                channels = cfg.get("channels", {})
                onboarding = cfg.get("onboarding", {})
                role_choices = [("0", "-- none --")] + [
                    (str(role.id), role.name[:80])
                    for role in sorted(guild.roles, key=lambda r: r.position, reverse=True)
                ]
                channel_choices = [("0", "-- none --")] + [
                    (str(channel.id), f"#{channel.name}") for channel in guild.text_channels
                ]
                category_choices = [("0", "-- no category --")] + [
                    (str(category.id), f"{category.name} ({category.id})") for category in guild.categories
                ]
                profile_choices = [(k, k) for k in sorted(wow_profiles.keys())] or [("retail", "retail")]
                form.profile_key.choices = profile_choices + [("__new__", "+ create new profile")]
                all_versions = ["retail", "classic", "classic_era", "mop_classic", "sod"]
                missing_versions = [v for v in all_versions if v not in wow_profiles]
                form.new_profile_version.choices = (
                    [(v, v) for v in missing_versions]
                    if missing_versions
                    else [("__none__", "all versions already configured")]
                )
                form.guest_role_id.choices = role_choices
                form.allied_role_id.choices = role_choices
                form.member_role_id.choices = role_choices
                form.onboarding_new_role_id.choices = role_choices
                form.onboarding_complete_role_id.choices = role_choices
                form.onboarding_channel_id.choices = channel_choices
                form.manual_review_channel_id.choices = channel_choices
                form.raid_guest_channel_id.choices = channel_choices
                form.rule_channel_id.choices = channel_choices
                form.target_category_id.choices = category_choices
                form.map_role_id.choices = role_choices
                form.map_rank_index.choices = [(str(i), str(i)) for i in range(10)]
                form.remove_rank_index.choices = [("__none__", "-- select --")] + [
                    (str(i), str(i)) for i in range(10)
                ]
                all_members = await self.config.all_members(guild)
                reg_choices = [("0", "-- none --")]
                for member_id, payload in all_members.items():
                    if payload.get("registration"):
                        m_obj = guild.get_member(int(member_id))
                        label = (
                            f"{m_obj.display_name} ({m_obj.id})"
                            if m_obj is not None
                            else f"{member_id}"
                        )
                        reg_choices.append((str(member_id), label))
                form.remove_registration_user_id.choices = reg_choices
                form.officer_character_notify_channel_id.choices = channel_choices
                form.rank_protected_notify_channel_id.choices = channel_choices
                form.rank_lock_notify_channel_id.choices = channel_choices
                tmpl = cfg.get("templates", {})
                if method.upper() == "GET":
                    form.language.data = cfg.get("language", "de-DE")
                    form.profile_key.data = active_key
                    form.new_profile_version.data = (
                        missing_versions[0] if missing_versions else "__none__"
                    )
                    form.region.data = wow.get("region", "eu")
                    form.version.data = wow.get("version", "retail")
                    form.realm.data = wow.get("realm", "")
                    form.guild_name.data = wow.get("guild_name", "")
                    form.welcome_text_de.data = onboarding.get("welcome_text_de", "")
                    form.welcome_text_en.data = onboarding.get("welcome_text_en", "")
                    form.guest_role_id.data = str(roles.get("guest_role_id", 0))
                    form.allied_role_id.data = str(roles.get("allied_role_id", 0))
                    form.allied_guilds_enabled.data = bool(cfg.get("features", {}).get("allied_guilds", False))
                    form.allied_guilds.data = "\n".join(cfg.get("allied_guilds", []))
                    form.member_role_id.data = str(roles.get("member_role_id", 0))
                    form.onboarding_new_role_id.data = str(roles.get("onboarding_new_role_id", 0))
                    form.onboarding_complete_role_id.data = str(roles.get("onboarding_complete_role_id", 0))
                    form.onboarding_channel_id.data = str(channels.get("onboarding_channel_id", 0))
                    form.manual_review_channel_id.data = str(channels.get("manual_review_channel_id", 0))
                    form.raid_guest_channel_id.data = str(channels.get("raid_guest_channel_id", 0))
                    rules_cfg = cfg.get("rules", {})
                    form.rule_channel_id.data = str(rules_cfg.get("rule_channel_id", 0))
                    form.rule_emoji.data = str(rules_cfg.get("rule_emoji", "✅"))
                    form.create_guest_role.data = False
                    form.create_member_role.data = False
                    form.create_onboarding_new_role.data = False
                    form.create_onboarding_complete_role.data = False
                    form.create_onboarding_channel.data = False
                    form.create_manual_review_channel.data = False
                    form.create_raid_guest_channel.data = False
                    form.target_category_id.data = "0"
                    form.guest_role_name.data = "guest"
                    form.member_role_name.data = "guild-member"
                    form.onboarding_new_role_name.data = "onboarding-new"
                    form.onboarding_complete_role_name.data = "onboarding-complete"
                    form.onboarding_channel_name.data = "onboarding-private"
                    form.manual_review_channel_name.data = "wow-manual-review"
                    form.raid_guest_channel_name.data = "wow-raid-guests"
                    form.map_rank_index.data = "0"
                    form.map_rank_title.data = ""
                    form.map_role_id.data = "0"
                    form.remove_rank_index.data = "__none__"
                    form.remove_registration_user_id.data = "0"
                    form.confirm_remove_registration.data = False
                    form.officer_character_notify_channel_id.data = str(
                        channels.get("officer_character_notify_channel_id", 0)
                    )
                    form.duplicate_character_message.data = tmpl.get(
                        "duplicate_character_message",
                        "",
                    )
                    form.member_left_characters_notice.data = tmpl.get(
                        "member_left_characters_notice",
                        "",
                    )
                    form.admin_removed_char_dm.data = tmpl.get("admin_removed_char_dm", "")
                    form.rank_protected_notify_channel_id.data = str(
                        channels.get("rank_protected_notify_channel_id", 0)
                    )
                    pr_lines = (cfg.get("protected_rank_titles_by_profile") or {}).get(active_key, [])
                    if isinstance(pr_lines, str):
                        pr_lines = [pr_lines]
                    form.protected_rank_lines.data = (
                        "\n".join(str(x) for x in pr_lines) if isinstance(pr_lines, (list, tuple)) else ""
                    )
                    lk_lines = (cfg.get("locked_rank_titles_by_profile") or {}).get(active_key, [])
                    if isinstance(lk_lines, str):
                        lk_lines = [lk_lines]
                    form.locked_rank_lines.data = (
                        "\n".join(str(x) for x in lk_lines) if isinstance(lk_lines, (list, tuple)) else ""
                    )
                    form.protected_rank_sync_notice.data = tmpl.get("protected_rank_sync_notice", "")
                    form.rank_lock_notify_channel_id.data = str(
                        channels.get("rank_lock_notify_channel_id", 0)
                    )
                    form.rank_lock_officer_notice.data = tmpl.get("rank_lock_officer_notice", "")

                if form.load_profile.data:
                    selected_key = str(form.profile_key.data or "").strip().lower()
                    if selected_key and selected_key != "__new__" and selected_key in wow_profiles:
                        cfg["active_profile_key"] = selected_key
                        cfg["wow"] = wow_profiles[selected_key]
                        await self.config.guild(guild).set(cfg)
                        return {
                            "status": 0,
                            "notifications": [
                                {"message": f"Profile `{selected_key}` loaded.", "category": "success"}
                            ],
                            "redirect_url": kwargs.get("request_url"),
                        }
                    return {
                        "status": 0,
                        "notifications": [
                            {
                                "message": "Select an existing profile to load.",
                                "category": "warning",
                            }
                        ],
                        "redirect_url": kwargs.get("request_url"),
                    }

                if form.apply_rank_mapping.data:
                    profile_key_for_map = cfg.get("active_profile_key", active_key) or "retail"
                    rank_titles_by_profile = cfg.get("rank_titles_by_profile", {})
                    rank_mapping_by_profile = cfg.get("rank_mapping_by_profile", {})
                    profile_titles = rank_titles_by_profile.get(profile_key_for_map, {})
                    profile_mapping = rank_mapping_by_profile.get(profile_key_for_map, {})
                    rank_idx = str(form.map_rank_index.data or "0")
                    role_id = int(form.map_role_id.data or 0)
                    if role_id == 0:
                        return {
                            "status": 0,
                            "notifications": [{"message": "Please select a role for mapping.", "category": "warning"}],
                            "redirect_url": kwargs.get("request_url"),
                        }
                    rank_title = str(form.map_rank_title.data or "").strip()
                    if not rank_title:
                        rank_title = f"Rank {rank_idx}"
                    profile_titles[rank_idx] = rank_title
                    profile_mapping[rank_title] = role_id
                    rank_titles_by_profile[profile_key_for_map] = profile_titles
                    rank_mapping_by_profile[profile_key_for_map] = profile_mapping
                    cfg["rank_titles_by_profile"] = rank_titles_by_profile
                    cfg["rank_mapping_by_profile"] = rank_mapping_by_profile
                    await self.config.guild(guild).set(cfg)
                    role = guild.get_role(role_id)
                    role_name = role.mention if role else f"`{role_id}`"
                    return {
                        "status": 0,
                        "notifications": [
                            {
                                "message": f"Rank mapping set for {profile_key_for_map}: {rank_title} -> {role_name}",
                                "category": "success",
                            }
                        ],
                        "redirect_url": kwargs.get("request_url"),
                    }

                if form.remove_rank_mapping.data:
                    profile_key_for_map = cfg.get("active_profile_key", active_key) or "retail"
                    rank_idx = str(form.remove_rank_index.data or "__none__")
                    if rank_idx == "__none__":
                        return {
                            "status": 0,
                            "notifications": [{"message": "Select a rank index to remove.", "category": "warning"}],
                            "redirect_url": kwargs.get("request_url"),
                        }
                    rank_titles_by_profile = cfg.get("rank_titles_by_profile", {})
                    rank_mapping_by_profile = cfg.get("rank_mapping_by_profile", {})
                    profile_titles = rank_titles_by_profile.get(profile_key_for_map, {})
                    profile_mapping = rank_mapping_by_profile.get(profile_key_for_map, {})
                    rank_title = profile_titles.pop(rank_idx, f"Rank {rank_idx}")
                    profile_mapping.pop(rank_title, None)
                    profile_mapping.pop(f"Rank {rank_idx}", None)
                    rank_titles_by_profile[profile_key_for_map] = profile_titles
                    rank_mapping_by_profile[profile_key_for_map] = profile_mapping
                    cfg["rank_titles_by_profile"] = rank_titles_by_profile
                    cfg["rank_mapping_by_profile"] = rank_mapping_by_profile
                    await self.config.guild(guild).set(cfg)
                    return {
                        "status": 0,
                        "notifications": [
                            {"message": f"Removed rank mapping for index {rank_idx}.", "category": "success"}
                        ],
                        "redirect_url": kwargs.get("request_url"),
                    }

                if form.save_protected_ranks.data:
                    profile_key_for_prot = str(cfg.get("active_profile_key", active_key) or "retail")
                    lines = str(form.protected_rank_lines.data or "").splitlines()
                    cleaned = [ln.strip() for ln in lines if ln.strip()]
                    pr = cfg.setdefault("protected_rank_titles_by_profile", {})
                    pr[profile_key_for_prot] = cleaned
                    cfg["protected_rank_titles_by_profile"] = pr
                    lock_lines = str(form.locked_rank_lines.data or "").splitlines()
                    cleaned_lock = [ln.strip() for ln in lock_lines if ln.strip()]
                    lr = cfg.setdefault("locked_rank_titles_by_profile", {})
                    lr[profile_key_for_prot] = cleaned_lock
                    cfg["locked_rank_titles_by_profile"] = lr
                    ch = dict(cfg.get("channels") or {})
                    ch["rank_protected_notify_channel_id"] = int(
                        form.rank_protected_notify_channel_id.data or 0
                    )
                    ch["rank_lock_notify_channel_id"] = int(form.rank_lock_notify_channel_id.data or 0)
                    cfg["channels"] = ch
                    tmerge = cfg.setdefault("templates", {})
                    tmerge["protected_rank_sync_notice"] = str(
                        form.protected_rank_sync_notice.data or ""
                    ).strip()
                    tmerge["rank_lock_officer_notice"] = str(
                        form.rank_lock_officer_notice.data or ""
                    ).strip()
                    await self.config.guild(guild).set(cfg)
                    return {
                        "status": 0,
                        "notifications": [
                            {
                                "message": f"Protected ranks + rank-lock notices saved for profile `{profile_key_for_prot}`.",
                                "category": "success",
                            }
                        ],
                        "redirect_url": kwargs.get("request_url"),
                    }

                if form.remove_registration.data:
                    target_member_id = int(form.remove_registration_user_id.data or 0)
                    if target_member_id == 0:
                        return {
                            "status": 0,
                            "notifications": [{"message": "Select a registration entry to remove.", "category": "warning"}],
                            "redirect_url": kwargs.get("request_url"),
                        }
                    if not bool(form.confirm_remove_registration.data):
                        return {
                            "status": 0,
                            "notifications": [
                                {
                                    "message": "Please tick the confirmation checkbox before deleting.",
                                    "category": "warning",
                                }
                            ],
                            "redirect_url": kwargs.get("request_url"),
                        }
                    m_obj = guild.get_member(target_member_id)
                    if m_obj is not None:
                        await self.config.member(m_obj).registration.clear()
                        await self.config.member(m_obj).selected_game.clear()
                    return {
                        "status": 0,
                        "notifications": [
                            {"message": f"Removed registration for {target_member_id}.", "category": "success"}
                        ],
                        "redirect_url": kwargs.get("request_url"),
                    }

                if form.validate_on_submit():
                    cfg["language"] = form.language.data if form.language.data in ("de-DE", "en-US") else "de-DE"
                    if form.profile_key.data == "__new__":
                        selected_new = str(form.new_profile_version.data or "").strip().lower()
                        if selected_new == "__none__" or selected_new not in missing_versions:
                            return {
                                "status": 0,
                                "notifications": [
                                    {
                                        "message": "No free game version available for a new profile.",
                                        "category": "warning",
                                    }
                                ],
                                "redirect_url": kwargs.get("request_url"),
                            }
                        profile_key = selected_new
                    else:
                        profile_key = str(form.profile_key.data or form.version.data or "retail").strip().lower()
                    bot_setup = await self.config.bot_setup()
                    default_region = str(bot_setup.get("default_region", "eu")).strip().lower()
                    default_language = bot_setup.get("default_language", "de-DE")
                    if default_language in ("de-DE", "en-US"):
                        cfg["language"] = default_language
                    profile = {
                        "region": str(form.region.data or default_region).strip().lower(),
                        "version": str(form.version.data or profile_key or "retail").strip().lower(),
                        "realm": str(form.realm.data or "").strip(),
                        "guild_name": str(form.guild_name.data or "").strip(),
                    }
                    cfg.setdefault("wow_profiles", {})
                    cfg["wow_profiles"][profile_key] = profile
                    # Immediately switch active profile to the selected/new one.
                    cfg["active_profile_key"] = profile_key
                    cfg["wow"] = profile
                    cfg["onboarding"] = {
                        "welcome_text_de": str(form.welcome_text_de.data or "").strip(),
                        "welcome_text_en": str(form.welcome_text_en.data or "").strip(),
                    }
                    cfg["features"] = dict(cfg.get("features") or {})
                    cfg["features"]["allied_guilds"] = bool(form.allied_guilds_enabled.data)
                    guilds_lines = str(form.allied_guilds.data or "").splitlines()
                    cfg["allied_guilds"] = [ln.strip() for ln in guilds_lines if ln.strip()]
                    cfg["roles"] = {
                        "guest_role_id": int(form.guest_role_id.data or 0),
                        "allied_role_id": int(form.allied_role_id.data or 0),
                        "member_role_id": int(form.member_role_id.data or 0),
                        "onboarding_new_role_id": int(form.onboarding_new_role_id.data or 0),
                        "onboarding_complete_role_id": int(form.onboarding_complete_role_id.data or 0),
                    }
                    ch_merge = dict(cfg.get("channels") or {})
                    ch_merge.update(
                        {
                            "onboarding_channel_id": int(form.onboarding_channel_id.data or 0),
                            "manual_review_channel_id": int(form.manual_review_channel_id.data or 0),
                            "raid_guest_channel_id": int(form.raid_guest_channel_id.data or 0),
                            "officer_character_notify_channel_id": int(
                                form.officer_character_notify_channel_id.data or 0
                            ),
                            "rank_protected_notify_channel_id": int(
                                form.rank_protected_notify_channel_id.data or 0
                            ),
                            "rank_lock_notify_channel_id": int(
                                form.rank_lock_notify_channel_id.data or 0
                            ),
                        }
                    )
                    cfg["channels"] = ch_merge
                    cfg["rules"] = {
                        "rule_channel_id": int(form.rule_channel_id.data or 0),
                        "rule_emoji": str(form.rule_emoji.data or "✅").strip() or "✅",
                    }
                    cfg.setdefault("templates", {})
                    cfg["templates"]["duplicate_character_message"] = str(
                        form.duplicate_character_message.data or ""
                    ).strip()
                    cfg["templates"]["member_left_characters_notice"] = str(
                        form.member_left_characters_notice.data or ""
                    ).strip()
                    cfg["templates"]["admin_removed_char_dm"] = str(
                        form.admin_removed_char_dm.data or ""
                    ).strip()
                    cfg["templates"]["protected_rank_sync_notice"] = str(
                        form.protected_rank_sync_notice.data or ""
                    ).strip()
                    cfg["templates"]["rank_lock_officer_notice"] = str(
                        form.rank_lock_officer_notice.data or ""
                    ).strip()
                    lines_prot = str(form.protected_rank_lines.data or "").splitlines()
                    cleaned_prot = [ln.strip() for ln in lines_prot if ln.strip()]
                    pr_save = cfg.setdefault("protected_rank_titles_by_profile", {})
                    pr_save[profile_key] = cleaned_prot
                    cfg["protected_rank_titles_by_profile"] = pr_save
                    lock_lines_main = str(form.locked_rank_lines.data or "").splitlines()
                    cleaned_lock_main = [ln.strip() for ln in lock_lines_main if ln.strip()]
                    lr_main = cfg.setdefault("locked_rank_titles_by_profile", {})
                    lr_main[profile_key] = cleaned_lock_main
                    cfg["locked_rank_titles_by_profile"] = lr_main
                    notifications = []
                    category = None
                    try:
                        category = guild.get_channel(int(form.target_category_id.data or 0))
                        if category and not isinstance(category, discord.CategoryChannel):
                            category = None
                    except Exception:
                        category = None

                    role_create_map = [
                        ("guest_role_id", form.guest_role_name.data or "guest", bool(form.create_guest_role.data)),
                        (
                            "member_role_id",
                            form.member_role_name.data or "guild-member",
                            bool(form.create_member_role.data),
                        ),
                        (
                            "onboarding_new_role_id",
                            form.onboarding_new_role_name.data or "onboarding-new",
                            bool(form.create_onboarding_new_role.data),
                        ),
                        (
                            "onboarding_complete_role_id",
                            form.onboarding_complete_role_name.data or "onboarding-complete",
                            bool(form.create_onboarding_complete_role.data),
                        ),
                    ]
                    for key, role_name, should_create in role_create_map:
                        if cfg["roles"].get(key, 0) or not should_create:
                            continue
                        existing = discord.utils.get(guild.roles, name=role_name)
                        if existing is None:
                            existing = await guild.create_role(
                                name=role_name, reason="WoW dashboard auto-create role"
                            )
                            notifications.append(
                                {"message": f"Created role: {existing.name}", "category": "info"}
                            )
                        cfg["roles"][key] = existing.id

                    channel_create_map = [
                        (
                            "onboarding_channel_id",
                            form.onboarding_channel_name.data or "onboarding-private",
                            bool(form.create_onboarding_channel.data),
                        ),
                        (
                            "manual_review_channel_id",
                            form.manual_review_channel_name.data or "wow-manual-review",
                            bool(form.create_manual_review_channel.data),
                        ),
                        (
                            "raid_guest_channel_id",
                            form.raid_guest_channel_name.data or "wow-raid-guests",
                            bool(form.create_raid_guest_channel.data),
                        ),
                    ]
                    for key, channel_name, should_create in channel_create_map:
                        if cfg["channels"].get(key, 0) or not should_create:
                            continue
                        existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
                        if existing_channel is None:
                            existing_channel = await guild.create_text_channel(
                                name=channel_name,
                                category=category,
                                reason="WoW dashboard auto-create channel",
                            )
                            notifications.append(
                                {
                                    "message": f"Created channel: #{existing_channel.name}",
                                    "category": "info",
                                }
                            )
                        cfg["channels"][key] = existing_channel.id

                    await self.config.guild(guild).set(cfg)
                    await self._apply_onboarding_channel_permissions(guild)
                    return {
                        "status": 0,
                        "notifications": notifications + [{"message": "WoW guild settings saved.", "category": "success"}],
                        "redirect_url": kwargs.get("request_url"),
                    }

                active_profile_for_ui = cfg.get("active_profile_key", active_key)
                rank_titles_by_profile = cfg.get("rank_titles_by_profile", {})
                rank_mapping_by_profile = cfg.get("rank_mapping_by_profile", {})
                profile_titles_ui = rank_titles_by_profile.get(active_profile_for_ui, {})
                profile_mapping_ui = rank_mapping_by_profile.get(active_profile_for_ui, {})
                current_rank_rows = []
                for idx in range(10):
                    idx_s = str(idx)
                    title = profile_titles_ui.get(idx_s, f"Rank {idx}")
                    mapped_id = profile_mapping_ui.get(title) or profile_mapping_ui.get(f"Rank {idx}")
                    if mapped_id:
                        role_obj = guild.get_role(int(mapped_id))
                        mapped_label = (
                            html.escape(role_obj.name) if role_obj else f"`{mapped_id}`"
                        )
                    else:
                        mapped_label = "<em>fallback: member default role</em>"
                    current_rank_rows.append(
                        f"<tr><td>{idx}</td><td>{html.escape(str(title))}</td><td>{mapped_label}</td></tr>"
                    )
                current_rank_table = "".join(current_rank_rows)

                reg_count = 0
                all_members_ui = await self.config.all_members(guild)
                for _m_id, _payload in all_members_ui.items():
                    if _payload.get("registration"):
                        reg_count += 1

                source = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
.wow-wrap {{
  font-family: 'Inter', sans-serif;
  background: rgba(18, 23, 33, 0.6);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  padding: 24px;
  color: #f3e9d2;
  box-shadow: 0 8px 32px 0 rgba(0,0,0,.3);
}}
.wow-wrap h2, .wow-wrap h3 {{ color: #ffffff; margin: 4px 0 16px 0; font-weight: 600; letter-spacing: -0.02em; }}
.wow-wrap p {{ margin-top: 0; margin-bottom: 14px; line-height: 1.5; color: #a0aec0; }}
.wow-wrap label {{ color: #cbd5e0; font-weight: 500; font-size: 13.5px; margin-bottom: 6px; display: inline-block; }}
.wow-wrap input, .wow-wrap select, .wow-wrap textarea {{
  background: rgba(0, 0, 0, 0.25);
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  padding: 10px 14px;
  min-width: 360px;
  font-size: 14px;
  transition: all 0.2s ease;
  box-sizing: border-box;
}}
.wow-wrap input:focus, .wow-wrap select:focus, .wow-wrap textarea:focus {{
  outline: none;
  border-color: #4299e1;
  box-shadow: 0 0 0 3px rgba(66, 153, 225, 0.25);
  background: rgba(0, 0, 0, 0.35);
}}
.wow-wrap hr {{ border-color: rgba(255,255,255,0.08); opacity: 1; margin: 24px 0; }}
.wow-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 20px;
}}
.wow-card {{
  background: rgba(0, 0, 0, 0.15);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 10px;
  padding: 18px;
  transition: all 0.3s ease;
}}
.wow-card:hover {{
  background: rgba(0, 0, 0, 0.2);
  border-color: rgba(255, 255, 255, 0.1);
  box-shadow: 0 4px 12px rgba(0,0,0,0.2);
}}
.wow-meta {{
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 20px;
}}
.wow-badge {{
  background: rgba(66, 153, 225, 0.15);
  border: 1px solid rgba(66, 153, 225, 0.3);
  color: #63b3ed;
  border-radius: 999px;
  padding: 6px 14px;
  font-size: 13px;
  font-weight: 500;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}}
.wow-table {{
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  margin-top: 12px;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid rgba(255,255,255,0.06);
}}
.wow-table th, .wow-table td {{
  border-bottom: 1px solid rgba(255,255,255,0.06);
  padding: 12px 14px;
  text-align: left;
  font-size: 13.5px;
  background: rgba(0,0,0,0.15);
}}
.wow-table th {{
  background: rgba(0,0,0,0.25);
  font-weight: 600;
  color: #a0aec0;
  text-transform: uppercase;
  font-size: 12px;
  letter-spacing: 0.05em;
}}
</style>
<div class="wow-wrap">
  <h2>WoW Guild Settings</h2>
  <p>Settings for <b>{guild.name}</b> - For the Horde/Alliance dashboard mode.</p>
  <div class="wow-meta">
    <span class="wow-badge">Active profile: <b>{active_profile_for_ui}</b></span>
    <span class="wow-badge">Registrations stored: <b>{reg_count}</b></span>
    <span class="wow-badge">Configured profiles: <b>{len(wow_profiles)}</b></span>
  </div>
  <form method="post">
    {form.hidden_tag()}
    <div class="wow-grid">
      <div class="wow-card">
        <h3>Profile</h3>
        <p><label>Language</label><br>{form.language()}</p>
        <p><label>WoW Profile</label><br>{form.profile_key()}</p>
        <p><small>Select existing profile or choose <b>+ create new profile</b>.</small></p>
        <p><label>Create New Profile For Version</label><br>{form.new_profile_version()}</p>
        <p>{form.load_profile()}</p>
        <p><label>Profile Region</label><br>{form.region()}<br><small>eu, us, kr, tw</small></p>
        <p><label>Profile Version</label><br>{form.version()}</p>
        <p><label>Realm</label><br>{form.realm()}</p>
        <p><label>Guild Name</label><br>{form.guild_name()}</p>
      </div>

      <div class="wow-card">
        <h3>Onboarding Texts</h3>
        <p><label>Onboarding Text DE</label><br>{form.welcome_text_de()}</p>
        <p><label>Onboarding Text EN</label><br>{form.welcome_text_en()}</p>
        <h3>Rules</h3>
        <p><small>Onboarding endet mit einem Hinweis in Thread/DM: Nutzer gehen in diesen Kanal und reagieren mit dem Emoji auf die <b>bereits vorhandene</b> Regelnachricht. Der Bot postet <b>nichts</b> im Regel-Kanal.</small></p>
        <p><label>Rules Channel</label><br>{form.rule_channel_id()}</p>
        <p><label>Rules Confirmation Emoji</label><br>{form.rule_emoji()}<br><small>z. B. <b>✅</b> — muss mit der Reaktion auf dem bestehenden Regel-Post übereinstimmen</small></p>
      </div>

      <div class="wow-card">
        <h3>Discord Roles</h3>
        <p><label>Guest Role</label><br>{form.guest_role_id()}<br><label>{form.create_guest_role()} Auto-create</label></p>
        <p><label>Member Role</label><br>{form.member_role_id()}<br><label>{form.create_member_role()} Auto-create</label></p>
        <p><label>Onboarding New Role</label><br>{form.onboarding_new_role_id()}<br><label>{form.create_onboarding_new_role()} Auto-create</label></p>
        <p><label>Onboarding Complete Role</label><br>{form.onboarding_complete_role_id()}<br><label>{form.create_onboarding_complete_role()} Auto-create</label></p>
      </div>

      <div class="wow-card">
        <h3>Verbündete Gilden</h3>
        <p><label>{form.allied_guilds_enabled()} Verbündete Gilden abfragen</label></p>
        <p><label>Rolle für Verbündete</label><br>{form.allied_role_id()}</p>
        <p><label>Verbündete Gilden (eine pro Zeile)</label><br>{form.allied_guilds(rows=6)}</p>
      </div>

      <div class="wow-card">
        <h3>Discord Channels</h3>
        <p><label>Onboarding Channel</label><br>{form.onboarding_channel_id()}<br><label>{form.create_onboarding_channel()} Auto-create</label></p>
        <p><label>Manual Review Channel</label><br>{form.manual_review_channel_id()}<br><label>{form.create_manual_review_channel()} Auto-create</label></p>
        <p><label>Raid Guest Channel</label><br>{form.raid_guest_channel_id()}<br><label>{form.create_raid_guest_channel()} Auto-create</label></p>
        <p><label>Officer notify channel</label><br>{form.officer_character_notify_channel_id()}<br><small>Leave / member quit: notice if linked WoW chars existed</small></p>
        <p><label>Protected-rank notify channel</label><br>{form.rank_protected_notify_channel_id()}<br><small>Hier erscheint der <b>Protected</b>-Hinweis, wenn jemand einen geschützten Ingame-Rang hat (kein automatischer Discord-Rang).</small></p>
        <p><label>Rank-lock notify channel</label><br>{form.rank_lock_notify_channel_id()}<br><small>Hier erscheint der <b>Rank-Lock</b>-Hinweis (eigenes Template). <b>0</b> = gleicher Kanal wie Protected.</small></p>
      </div>

      <div class="wow-card">
        <h3>Character linking messages</h3>
        <p><small>Slash: <code>/wow-user</code> (Panel), <code>/wow-admin</code> (Officer), <code>/wow-masteradmin</code>.</small></p>
        <p><label>Duplicate / already linked (use &#123;detail&#125;)</label><br>{form.duplicate_character_message(rows=4)}</p>
        <p><label>Member left notice (&#123;user&#125;, &#123;username&#125;, &#123;chars&#125;)</label><br>{form.member_left_characters_notice(rows=3)}</p>
        <p><label>Officer removal DM (&#123;chars&#125;, &#123;reason&#125;, &#123;officer&#125;)</label><br>{form.admin_removed_char_dm(rows=3)}</p>
      </div>

      <div class="wow-card">
        <h3>Auto-Create Names</h3>
        <p><label>Target Category</label><br>{form.target_category_id()}</p>
        <p><label>Guest Role Name</label><br>{form.guest_role_name()}</p>
        <p><label>Member Role Name</label><br>{form.member_role_name()}</p>
        <p><label>Onboarding New Role Name</label><br>{form.onboarding_new_role_name()}</p>
        <p><label>Onboarding Complete Role Name</label><br>{form.onboarding_complete_role_name()}</p>
        <p><label>Onboarding Channel Name</label><br>{form.onboarding_channel_name()}</p>
        <p><label>Manual Review Channel Name</label><br>{form.manual_review_channel_name()}</p>
        <p><label>Raid Guest Channel Name</label><br>{form.raid_guest_channel_name()}</p>
      </div>

      <div class="wow-card">
        <h3>Rank Mapping (0-9)</h3>
        <p><small>Per active profile. Missing mapping uses member default role.</small></p>
        <table class="wow-table">
          <thead><tr><th>Index</th><th>Title</th><th>Mapped Role</th></tr></thead>
          <tbody>{current_rank_table}</tbody>
        </table>
        <p><label>Rank Index</label><br>{form.map_rank_index()}</p>
        <p><label>Rank Title (optional)</label><br>{form.map_rank_title()}</p>
        <p><label>Discord Role</label><br>{form.map_role_id()}</p>
        <p>{form.apply_rank_mapping()}</p>
        <p><label>Remove by Rank Index</label><br>{form.remove_rank_index()}</p>
        <p>{form.remove_rank_mapping()}</p>
      </div>

      <div class="wow-card">
        <h3>Protected &amp; Rank-Lock (aktives Profil)</h3>
        <p><small><b>Technisch gleich:</b> Steht der Ingame-Rang auf einer der beiden Listen, setzt der Bot <b>keine</b> automatische Discord-Rangrolle (laut Rank-Mapping). Unterschied ist nur <b>Organisation</b>: eigener Offiziers-Kanal und eigenes Hinweis-Template — damit ihr z. B. „normale“ geschützte Ränge und bewusst gesperrte Ränge (Rank-Lock) getrennt behandeln könnt.</small></p>
        <p><small><b>Protected</b> — für Ränge, die ihr grundsätzlich nicht per Bot syncen wollt (z. B. sensible Offiziersstufen, manuelle Discord-Vergabe). Hinweis geht an den <b>Protected-rank notify</b>-Kanal, Text aus der Vorlage „Protected“.</small></p>
        <p><small><b>Rank-Lock</b> — für konkrete Ingame-Ränge, die der Bot nie zuweisen soll (z. B. „Kriegsfürst“). Liste ist pro Mitglied nicht nötig: Es zählt nur der WoW-Rang. Hinweis an <b>Rank-lock notify</b>-Kanal (oder 0 = wie Protected), Text aus der Vorlage „Rank-Lock“.</small></p>
        <p><small><b>Matching:</b> Pro Zeile Rangtitel, API-interner Name oder Index <b>0–9</b> (wie in der Rang-Mapping-Tabelle). Ein Eintrag pro Zeile. Vor dem Bearbeiten immer das richtige Profil laden.</small></p>
        <p><label>Liste Protected-Ränge</label><br>{form.protected_rank_lines(rows=8)}</p>
        <p><label>Liste Rank-Lock-Ränge</label><br>{form.locked_rank_lines(rows=8)}</p>
        <p><label>Vorlage Offiziers-Hinweis (Protected)</label><br>{form.protected_rank_sync_notice(rows=3)}</p>
        <p><label>Vorlage Offiziers-Hinweis (Rank-Lock)</label><br>{form.rank_lock_officer_notice(rows=3)}</p>
        <p><small>Platzhalter: &#123;member&#125;, &#123;user&#125;, &#123;username&#125;, &#123;user_id&#125;, &#123;game&#125;, &#123;char&#125;, &#123;rank&#125;, &#123;profile&#125;, &#123;detail&#125;</small></p>
        <p>{form.save_protected_ranks()}</p>
      </div>
    </div>
    <hr>
    <div class="wow-card">
      <h3>Registration Cleanup</h3>
      <p><label>Registration Entry</label><br>{form.remove_registration_user_id()}</p>
      <p><label>{form.confirm_remove_registration()} Confirm permanent deletion</label></p>
      <p>{form.remove_registration()}</p>
    </div>
    <p>{form.submit()}</p>
  </form>
</div>
"""
                return {"status": 0, "web_content": {"source": source, "standalone": True}}

            return {
                "status": 0,
                "web_content": {
                    "source": (
                        "<div style='padding:12px;'>"
                        "<h2>WoW Guild Settings</h2>"
                        "<p>Use POST on this page endpoint to update values.</p>"
                        "<h3>Current Config</h3>"
                        f"<pre>{json.dumps(cfg, indent=2)}</pre>"
                        "<h3>Payload Example</h3>"
                        "<pre>{\n"
                        '  "language": "de-DE",\n'
                        '  "region": "eu",\n'
                        '  "version": "retail",\n'
                        '  "realm": "my-realm",\n'
                        '  "guild_name": "my-guild",\n'
                        '  "guest_role_id": 0,\n'
                        '  "member_role_id": 0,\n'
                        '  "onboarding_new_role_id": 0,\n'
                        '  "onboarding_complete_role_id": 0,\n'
                        '  "onboarding_channel_id": 0,\n'
                        '  "manual_review_channel_id": 0,\n'
                        '  "raid_guest_channel_id": 0\n'
                        "}</pre>"
                        "</div>"
                    ),
                    "standalone": True,
                },
            }
        except Exception as e:
            return {
                "status": 0,
                "error_code": 500,
                "message": f"Guild page failed: {e}",
                "error_message": traceback.format_exc(limit=2),
            }


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WowGuildAutomation(bot))


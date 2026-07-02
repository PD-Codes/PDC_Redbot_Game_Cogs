"""Modals and small views for /wow-masteradmin (keeps wowguild_automation.py smaller)."""

from __future__ import annotations

from typing import TYPE_CHECKING, List

import discord

from .pdc_dashboard import tr_lang

if TYPE_CHECKING:
    from .wowguild_automation import WowGuildAutomation


class GuildSettingsModal(discord.ui.Modal, title="WoW-Gildenprofil (aktives Profil)"):
    region = discord.ui.TextInput(label="Region", placeholder="eu", default="eu", max_length=8, required=True)
    version = discord.ui.TextInput(
        label="Version",
        placeholder="retail oder mop_classic",
        default="retail",
        max_length=32,
        required=True,
    )
    realm = discord.ui.TextInput(label="Realm (Slug)", placeholder="tarren-mill", max_length=64, required=True)
    guild_name = discord.ui.TextInput(label="Gildenname (exakt)", max_length=64, required=True)
    language = discord.ui.TextInput(
        label="Bot-Sprache",
        placeholder="de-DE oder en-US",
        default="en-US",
        max_length=8,
        required=True,
    )

    def __init__(self, cog: "WowGuildAutomation", guild: discord.Guild, lang: str = "en-US") -> None:
        super().__init__(title=tr_lang(lang, "WoW-Gildenprofil (aktives Profil)", "WoW guild profile (active profile)"))
        self.cog = cog
        self.guild = guild
        self.lang = lang
        self.region.label = tr_lang(lang, "Region", "Region")
        self.version.label = tr_lang(lang, "Version", "Version")
        self.version.placeholder = tr_lang(lang, "retail oder mop_classic", "retail or mop_classic")
        self.realm.label = tr_lang(lang, "Realm (Slug)", "Realm (slug)")
        self.guild_name.label = tr_lang(lang, "Gildenname (exakt)", "Guild name (exact)")
        self.language.label = tr_lang(lang, "Bot-Sprache", "Bot language")
        self.language.placeholder = tr_lang(lang, "de-DE oder en-US", "de-DE or en-US")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        lang = str(self.language.value).strip()
        if lang not in ("de-DE", "en-US"):
            lang = "en-US"
        version_key = str(self.version.value).lower().strip().replace("-", "_")
        if version_key in ("mop", "classic_mop"):
            version_key = "mop_classic"
        profile = {
            "region": str(self.region.value).lower().strip(),
            "version": version_key,
            "realm": str(self.realm.value).strip(),
            "guild_name": str(self.guild_name.value).strip(),
        }
        cfg = await self.cog._guild_config(self.guild)
        cfg["language"] = lang
        cfg.setdefault("wow_profiles", {})
        cfg["wow_profiles"][version_key] = profile
        cfg["wow"] = profile
        cfg["active_profile_key"] = version_key
        await self.cog.config.guild(self.guild).set(cfg)
        glang = await self.cog._guild_lang(self.guild)
        await interaction.response.send_message(
            tr_lang(glang, "Gildenprofil gespeichert.", "Guild profile saved."),
            ephemeral=True,
        )


class BotSetupModal(discord.ui.Modal, title="Blizzard API (Bot-Besitzer)"):
    client_id = discord.ui.TextInput(label="Client ID", max_length=128, required=True)
    client_secret = discord.ui.TextInput(
        label="Client Secret",
        style=discord.TextStyle.short,
        max_length=128,
        required=True,
    )

    def __init__(self, cog: "WowGuildAutomation", lang: str = "en-US") -> None:
        super().__init__(title=tr_lang(lang, "Blizzard API (Bot-Besitzer)", "Blizzard API (bot owner)"))
        self.cog = cog
        self.lang = lang
        self.client_id.label = tr_lang(lang, "Client ID", "Client ID")
        self.client_secret.label = tr_lang(lang, "Client Secret", "Client Secret")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(interaction.guild) if interaction.guild else "en-US"
        # Re-check on submit: API credentials are strictly bot-owner only.
        if not await self.cog.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                tr_lang(glang, "Nur Bot-Besitzer.", "Bot owner only."), ephemeral=True
            )
            return
        data = await self.cog.config.bot_setup()
        owners = set(data.get("owner_ids", []))
        owners.add(interaction.user.id)
        data["owner_ids"] = list(owners)
        data["client_id"] = str(self.client_id.value).strip()
        data["client_secret"] = str(self.client_secret.value).strip()
        await self.cog.config.bot_setup.set(data)
        self.cog.blizzard.client_id = data["client_id"]
        self.cog.blizzard.client_secret = data["client_secret"]
        await interaction.response.send_message(
            tr_lang(glang, "Blizzard API gespeichert.", "Blizzard API saved."), ephemeral=True
        )


class MasterSetupModal(discord.ui.Modal, title="Globale Defaults"):
    default_language = discord.ui.TextInput(label="Sprache", default="en-US", max_length=8, required=True)
    default_region = discord.ui.TextInput(label="Region", default="eu", max_length=8, required=True)
    default_version = discord.ui.TextInput(label="Version", default="retail", max_length=32, required=True)
    dashboard_enabled = discord.ui.TextInput(
        label="Dashboard an (ja/nein)",
        placeholder="ja",
        default="ja",
        max_length=4,
        required=True,
    )

    def __init__(self, cog: "WowGuildAutomation", lang: str = "en-US") -> None:
        super().__init__(title=tr_lang(lang, "Globale Defaults", "Global defaults"))
        self.cog = cog
        self.lang = lang
        self.default_language.label = tr_lang(lang, "Sprache", "Language")
        self.default_region.label = tr_lang(lang, "Region", "Region")
        self.default_version.label = tr_lang(lang, "Version", "Version")
        self.dashboard_enabled.label = tr_lang(lang, "Dashboard an (ja/nein)", "Dashboard on (yes/no)")
        self.dashboard_enabled.placeholder = tr_lang(lang, "ja", "yes")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(interaction.guild) if interaction.guild else "en-US"
        # Re-check on submit: global bot defaults are strictly bot-owner only.
        if not await self.cog.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                tr_lang(glang, "Nur Bot-Besitzer.", "Bot owner only."), ephemeral=True
            )
            return
        lang = str(self.default_language.value).strip()
        if lang not in ("de-DE", "en-US"):
            lang = "en-US"
        en = str(self.dashboard_enabled.value).lower().strip() in ("ja", "yes", "true", "1", "on")
        data = await self.cog.config.bot_setup()
        data["default_language"] = lang
        data["default_region"] = str(self.default_region.value).strip().lower()
        data["default_version"] = str(self.default_version.value).strip().lower()
        data["dashboard_enabled"] = en
        await self.cog.config.bot_setup.set(data)
        await interaction.response.send_message(
            tr_lang(glang, "Master-Defaults gespeichert.", "Master defaults saved."),
            ephemeral=True,
        )


class SetRankTitleModal(discord.ui.Modal, title="Rangtitel (Index 0–9)"):
    rank_index = discord.ui.TextInput(label="Index", placeholder="0", max_length=2, required=True)
    title = discord.ui.TextInput(label="Anzeigetitel", max_length=64, required=True)

    def __init__(self, cog: "WowGuildAutomation", guild: discord.Guild, lang: str = "en-US") -> None:
        super().__init__(title=tr_lang(lang, "Rangtitel (Index 0–9)", "Rank title (index 0–9)"))
        self.cog = cog
        self.guild = guild
        self.lang = lang
        self.rank_index.label = tr_lang(lang, "Index", "Index")
        self.title.label = tr_lang(lang, "Anzeigetitel", "Display title")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        glang = await self.cog._guild_lang(self.guild)
        try:
            idx = int(str(self.rank_index.value).strip())
        except ValueError:
            await interaction.response.send_message(
                tr_lang(glang, "Ungültiger Index.", "Invalid index."), ephemeral=True
            )
            return
        if idx < 0 or idx > 9:
            await interaction.response.send_message(
                tr_lang(glang, "Index 0–9.", "Index 0–9."), ephemeral=True
            )
            return
        cfg = await self.cog._guild_config(self.guild)
        pk = cfg.get("active_profile_key", "retail") or "retail"
        titles = cfg.get("rank_titles_by_profile", {}).get(pk, {})
        if not isinstance(titles, dict):
            titles = {}
        titles[str(idx)] = str(self.title.value).strip()
        cfg.setdefault("rank_titles_by_profile", {})[pk] = titles
        await self.cog.config.guild(self.guild).set(cfg)
        await interaction.response.send_message(
            tr_lang(
                glang,
                f"Rang {idx}: `{self.title.value}` gespeichert.",
                f"Rank {idx}: `{self.title.value}` saved.",
            ),
            ephemeral=True,
        )


class MapRankModal(discord.ui.Modal, title="Rang → Discord-Rolle"):
    rank_name = discord.ui.TextInput(label="Rangname (wie Mapping)", max_length=64, required=True)
    role_id = discord.ui.TextInput(label="Rollen-ID", placeholder="1234567890", max_length=22, required=True)

    def __init__(self, cog: "WowGuildAutomation", guild: discord.Guild, lang: str = "en-US") -> None:
        super().__init__(title=tr_lang(lang, "Rang → Discord-Rolle", "Rank → Discord role"))
        self.cog = cog
        self.guild = guild
        self.lang = lang
        self.rank_name.label = tr_lang(lang, "Rangname (wie Mapping)", "Rank name (as in mapping)")
        self.role_id.label = tr_lang(lang, "Rollen-ID", "Role ID")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        glang = await self.cog._guild_lang(self.guild)
        try:
            rid = int(str(self.role_id.value).strip())
        except ValueError:
            await interaction.response.send_message(
                tr_lang(glang, "Ungültige Rollen-ID.", "Invalid role ID."), ephemeral=True
            )
            return
        role = self.guild.get_role(rid)
        if not role:
            await interaction.response.send_message(
                tr_lang(glang, "Rolle nicht gefunden.", "Role not found."), ephemeral=True
            )
            return
        cfg = await self.cog._guild_config(self.guild)
        pk = cfg.get("active_profile_key", "retail") or "retail"
        m = cfg.get("rank_mapping_by_profile", {}).get(pk, {})
        if not isinstance(m, dict):
            m = {}
        m[str(self.rank_name.value).strip()] = rid
        cfg.setdefault("rank_mapping_by_profile", {})[pk] = m
        await self.cog.config.guild(self.guild).set(cfg)
        await interaction.response.send_message(
            tr_lang(
                glang,
                f"Mapping: `{self.rank_name.value}` → {role.mention}",
                f"Mapping: `{self.rank_name.value}` → {role.mention}",
            ),
            ephemeral=True,
        )


class SyncIntervalModal(discord.ui.Modal, title="Auto Rang-Sync Intervall"):
    minutes = discord.ui.TextInput(
        label="Minuten (0 = aus)",
        placeholder="60",
        default="0",
        max_length=5,
        required=True,
    )

    def __init__(self, cog: "WowGuildAutomation", guild: discord.Guild, lang: str = "en-US") -> None:
        super().__init__(title=tr_lang(lang, "Auto Rang-Sync Intervall", "Auto rank-sync interval"))
        self.cog = cog
        self.guild = guild
        self.lang = lang
        self.minutes.label = tr_lang(lang, "Minuten (0 = aus)", "Minutes (0 = off)")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        glang = await self.cog._guild_lang(self.guild)
        try:
            m = int(str(self.minutes.value).strip())
        except ValueError:
            await interaction.response.send_message(
                tr_lang(glang, "Ungültige Zahl.", "Invalid number."), ephemeral=True
            )
            return
        if m < 0:
            m = 0
        cfg = await self.cog._guild_config(self.guild)
        cfg["rank_sync_interval_minutes"] = m
        await self.cog.config.guild(self.guild).set(cfg)
        await interaction.response.send_message(
            tr_lang(
                glang,
                f"Intervall: **{m}** Min. (0 = kein automatischer Sync).",
                f"Interval: **{m}** min (0 = no automatic sync).",
            ),
            ephemeral=True,
        )


class OnboardingSetupModal(discord.ui.Modal, title="Onboarding: Kanal & Rollen"):
    channel_id = discord.ui.TextInput(
        label="Onboarding-Channel-ID (0 = unverändert)",
        placeholder="0",
        default="0",
        max_length=22,
        required=True,
    )
    new_role_id = discord.ui.TextInput(
        label="Rolle „onboarding-new“ ID",
        placeholder="0",
        default="0",
        max_length=22,
        required=True,
    )
    complete_role_id = discord.ui.TextInput(
        label="Rolle „onboarding-complete“ ID",
        placeholder="0",
        default="0",
        max_length=22,
        required=True,
    )

    def __init__(self, cog: "WowGuildAutomation", guild: discord.Guild, lang: str = "en-US") -> None:
        super().__init__(title=tr_lang(lang, "Onboarding: Kanal & Rollen", "Onboarding: channel & roles"))
        self.cog = cog
        self.guild = guild
        self.lang = lang
        self.channel_id.label = tr_lang(lang, "Onboarding-Channel-ID (0 = unverändert)", "Onboarding channel ID (0 = unchanged)")
        self.new_role_id.label = tr_lang(lang, "Rolle „onboarding-new“ ID", "Role „onboarding-new“ ID")
        self.complete_role_id.label = tr_lang(lang, "Rolle „onboarding-complete“ ID", "Role „onboarding-complete“ ID")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return

        def _parse_id(raw: str) -> int:
            s = str(raw).strip()
            if not s or s == "0":
                return 0
            try:
                return int(s)
            except ValueError:
                return -1

        glang = await self.cog._guild_lang(self.guild)
        ch = _parse_id(self.channel_id.value)
        nr = _parse_id(self.new_role_id.value)
        cr = _parse_id(self.complete_role_id.value)
        if -1 in (ch, nr, cr):
            await interaction.response.send_message(
                tr_lang(glang, "Ungültige IDs.", "Invalid IDs."), ephemeral=True
            )
            return
        cfg = await self.cog._guild_config(self.guild)
        channels = dict(cfg.get("channels") or {})
        roles = dict(cfg.get("roles") or {})
        if ch > 0:
            channels["onboarding_channel_id"] = ch
        if nr > 0:
            roles["onboarding_new_role_id"] = nr
        if cr > 0:
            roles["onboarding_complete_role_id"] = cr
        cfg["channels"] = channels
        cfg["roles"] = roles
        await self.cog.config.guild(self.guild).set(cfg)
        await self.cog._apply_onboarding_channel_permissions(self.guild)
        await interaction.response.send_message(
            tr_lang(
                glang,
                "Onboarding-IDs gespeichert und Kanalrechte angewendet (soweit möglich).",
                "Onboarding IDs saved and channel permissions applied (where possible).",
            ),
            ephemeral=True,
        )


class AdminPickOneMemberView(discord.ui.View):
    """Pick a single member — e.g. simulate-join, delete registration, single rank sync."""

    def __init__(
        self,
        cog: "WowGuildAutomation",
        guild: discord.Guild,
        officer: discord.Member,
        *,
        mode: str,
        lang: str = "en-US",
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.officer = officer
        self.mode = mode
        self.lang = lang
        self.pick.placeholder = tr_lang(lang, "Mitglied wählen", "Pick a member")

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Mitglied wählen", min_values=1, max_values=1)
    async def pick(self, interaction: discord.Interaction, select: discord.ui.UserSelect) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.officer.id:
            await interaction.response.send_message(
                tr_lang(glang, "Nur für dich.", "For you only."), ephemeral=True
            )
            return
        u = select.values[0]
        member = self.guild.get_member(u.id)
        if member is None:
            await interaction.response.send_message(
                tr_lang(glang, "Mitglied nicht auf dem Server.", "Member not on the server."),
                ephemeral=True,
            )
            return
        if self.mode == "simulate_join":
            await interaction.response.defer(ephemeral=True)
            await self.cog._run_onboarding_flow(member, simulated=True)
            await interaction.followup.send(
                tr_lang(
                    glang,
                    f"Onboarding-Simulation für {member.mention} fertig.",
                    f"Onboarding simulation for {member.mention} done.",
                ),
                ephemeral=True,
            )
            self.stop()
            return
        if self.mode == "remove_registration":
            await self.cog.config.member(member).registration.clear()
            await self.cog.config.member(member).selected_game.clear()
            await interaction.response.send_message(
                tr_lang(
                    glang,
                    f"Registrierung von {member.mention} gelöscht.",
                    f"Registration of {member.mention} deleted.",
                ),
                ephemeral=True,
            )
            self.stop()
            return
        if self.mode == "sync_rank_member":
            await interaction.response.defer(ephemeral=True)
            text = await self.cog._slash_admin_sync_report_for_member(self.guild, member)
            await interaction.followup.send(text[:1900], ephemeral=True)
            self.stop()
            return
        await interaction.response.send_message(
            tr_lang(glang, "Unbekannt.", "Unknown."), ephemeral=True
        )


class RankLockAddModal(discord.ui.Modal, title="Rank-Lock: Rang sperren"):
    line = discord.ui.TextInput(
        label="Rangname oder Index (0–9), wie in der WebUI",
        placeholder="z.B. Kriegsfürst oder 3",
        max_length=64,
        required=True,
    )

    def __init__(self, cog: "WowGuildAutomation", guild: discord.Guild, lang: str = "en-US") -> None:
        super().__init__(title=tr_lang(lang, "Rank-Lock: Rang sperren", "Rank lock: lock a rank"))
        self.cog = cog
        self.guild = guild
        self.lang = lang
        self.line.label = tr_lang(lang, "Rangname oder Index (0–9), wie in der WebUI", "Rank name or index (0–9), as in the WebUI")
        self.line.placeholder = tr_lang(lang, "z.B. Kriegsfürst oder 3", "e.g. Warlord or 3")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        glang = await self.cog._guild_lang(self.guild)
        new_l = str(self.line.value).strip()
        if not new_l:
            await interaction.response.send_message(
                tr_lang(glang, "Leer.", "Empty."), ephemeral=True
            )
            return
        cfg = await self.cog._guild_config(self.guild)
        pk = str(cfg.get("active_profile_key") or "retail")
        lr = dict(cfg.get("locked_rank_titles_by_profile") or {})
        cur = lr.get(pk)
        lines: List[str]
        if cur is None:
            lines = []
        elif isinstance(cur, str):
            lines = [cur]
        elif isinstance(cur, (list, tuple)):
            lines = [str(x).strip() for x in cur if str(x).strip()]
        else:
            lines = []
        low = {x.lower() for x in lines}
        if new_l.lower() not in low:
            lines.append(new_l)
        lr[pk] = lines
        cfg["locked_rank_titles_by_profile"] = lr
        await self.cog.config.guild(self.guild).set(cfg)
        await interaction.response.send_message(
            tr_lang(
                glang,
                f"Rank-Lock für **{new_l}** gespeichert (aktives Profil `{pk}`).",
                f"Rank lock for **{new_l}** saved (active profile `{pk}`).",
            ),
            ephemeral=True,
        )


class RankLockRemoveModal(discord.ui.Modal, title="Rank-Lock: Eintrag entfernen"):
    line = discord.ui.TextInput(
        label="Exakt oder Teil des Eintrags (Groß/Klein egal)",
        placeholder="z.B. Kriegsfürst",
        max_length=64,
        required=True,
    )

    def __init__(self, cog: "WowGuildAutomation", guild: discord.Guild, lang: str = "en-US") -> None:
        super().__init__(title=tr_lang(lang, "Rank-Lock: Eintrag entfernen", "Rank lock: remove entry"))
        self.cog = cog
        self.guild = guild
        self.lang = lang
        self.line.label = tr_lang(lang, "Exakt oder Teil des Eintrags (Groß/Klein egal)", "Exact or part of the entry (case-insensitive)")
        self.line.placeholder = tr_lang(lang, "z.B. Kriegsfürst", "e.g. Warlord")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        glang = await self.cog._guild_lang(self.guild)
        needle = str(self.line.value).strip().lower()
        if not needle:
            await interaction.response.send_message(
                tr_lang(glang, "Leer.", "Empty."), ephemeral=True
            )
            return
        cfg = await self.cog._guild_config(self.guild)
        pk = str(cfg.get("active_profile_key") or "retail")
        lr = dict(cfg.get("locked_rank_titles_by_profile") or {})
        cur = lr.get(pk)
        if isinstance(cur, str):
            lines = [cur] if cur.strip() else []
        elif isinstance(cur, (list, tuple)):
            lines = [str(x).strip() for x in cur if str(x).strip()]
        else:
            lines = []
        before = len(lines)
        lines = [x for x in lines if needle not in x.lower()]
        removed = before - len(lines)
        if removed == 0:
            await interaction.response.send_message(
                tr_lang(
                    glang,
                    f"Kein Treffer in der Rank-Lock-Liste für Profil `{pk}`.",
                    f"No match in the rank-lock list for profile `{pk}`.",
                ),
                ephemeral=True,
            )
            return
        lr[pk] = lines
        cfg["locked_rank_titles_by_profile"] = lr
        await self.cog.config.guild(self.guild).set(cfg)
        await interaction.response.send_message(
            tr_lang(
                glang,
                f"**{removed}** Eintrag/Einträge aus Rank-Lock entfernt (`{pk}`).",
                f"**{removed}** entry/entries removed from rank lock (`{pk}`).",
            ),
            ephemeral=True,
        )

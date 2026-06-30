import asyncio
from typing import Any, Dict, List, Optional

import discord
from discord.ext import commands

from ..character_helpers import (
    game_label,
    merge_rank_sync_game_state,
    profile_key_to_link_game,
)
from ..functions.automations import RankSyncService
from ..officer_notifications import send_rank_lock_officer_notice, send_protected_rank_officer_notice
from ..pdc_dashboard import tr_lang

TEXTS: Dict[str, Dict[str, str]] = {
    "de-DE": {
        "lang_prompt": "Willkommen! Bitte waehle deine Sprache.",
        "lang_timeout": "Onboarding abgebrochen (Zeit abgelaufen).",
        "role_prompt": "Bist du Gast oder neues Gildenmitglied?",
        "guest_done": "Du wurdest als Gast markiert.",
        "guest_type_prompt": "Bist du ein reiner Gast oder von einer verbündeten Gilde?",
        "guest_pure": "Reiner Gast",
        "guest_allied": "Verbündete Gilde",
        "allied_guild_select": "Von welcher verbündeten Gilde bist du?",
        "allied_done": "Du wurdest als Verbündeter von `{guild}` markiert.",
        "rules_channel_hint": (
            "**Damit ist das Onboarding hier beendet.** Bitte gehe jetzt zu {rules_channel} und bestätige die Regeln mit {emoji} "
            "auf der **bereits vorhandenen** Regelnachricht (den bestehenden Post mit dem Hinweis zum Abhaken). "
            "Der Bot schreibt **nichts** in den Regel-Kanal — du reagierst nur auf die Nachricht, die schon da ist."
        ),
        "mainchar_prompt": "Bitte gib deinen Mainchar ein (Button -> Popup).",
        "game_prompt": "Fuer welches WoW-Spiel meldest du dich an?",
        "mainchar_timeout": "Kein Mainchar erhalten, Onboarding beendet.",
        "verified": "Verifizierung erfolgreich. Mainchar `{main}` gefunden, Ingame-Rang `{rank}`.",
        "manual": "Automatische Verifizierung nicht moeglich. Das Team wurde fuer manuelle Pruefung benachrichtigt.",
        "protected_rank": "Dein Char wurde gefunden. Der Ingame-Rang ist geschuetzt — ein Offizier teilt dir die passende Discord-Rolle mit.",
        "rank_locked_onboarding": (
            "Dein Ingame-Rang steht auf der **Rank-Lock**-Liste: Der Bot weist dafür keine Discord-Rolle zu. "
            "Bitte einen Offizier ansprechen."
        ),
        "rules": "Wichtig: Bitte lies die Serverregeln und bestaetige sie mit dem vorgegebenen Emoji.",
        "rules_confirm": "Regeln bestaetigen",
        "rules_timeout": "Zeit abgelaufen — Regeln nicht bestätigt. Bitte einen Admin kontaktieren oder erneut joinen.",
        "rules_fallback": "Kein Regel-Kanal konfiguriert — bitte hier bestätigen:",
    },
    "en-US": {
        "lang_prompt": "Welcome! Please choose your language.",
        "lang_timeout": "Onboarding cancelled (timeout).",
        "role_prompt": "Are you a guest or a new guild member?",
        "guest_done": "You are marked as a guest.",
        "guest_type_prompt": "Are you a pure guest or from an allied guild?",
        "guest_pure": "Pure Guest",
        "guest_allied": "Allied Guild",
        "allied_guild_select": "Which allied guild are you from?",
        "allied_done": "You have been marked as an ally from `{guild}`.",
        "rules_channel_hint": (
            "**This finishes onboarding here.** Please go to {rules_channel} now and confirm the rules with {emoji} "
            "on the **existing** rules post (the one that already asks for the checkmark). "
            "The bot **does not post** in the rules channel — you only react on the message that is already there."
        ),
        "mainchar_prompt": "Please enter your main character (button -> popup).",
        "game_prompt": "Which WoW game are you signing up for?",
        "mainchar_timeout": "No main character received, onboarding cancelled.",
        "verified": "Verification successful. Main character `{main}` found, ingame rank `{rank}`.",
        "manual": "Automatic verification failed. The team was notified for manual review.",
        "protected_rank": "Your character was found. The in-game rank is protected — an officer will assign your Discord role.",
        "rank_locked_onboarding": (
            "Your in-game rank is on the **rank-lock** list: the bot will not assign a Discord role for it. "
            "Please contact an officer."
        ),
        "rules": "Important: Please read the server rules and confirm with the required emoji.",
        "rules_confirm": "Confirm rules",
        "rules_timeout": "Timed out — rules not confirmed. Contact an admin or rejoin.",
        "rules_fallback": "No rules channel configured — confirm here:",
    },
}


class ChoiceView(discord.ui.View):
    def __init__(self, user_id: int, options: List[tuple[str, str]], timeout: int = 180) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.value: Optional[str] = None
        for label, value in options[:5]:
            self.add_item(ChoiceButton(label=label, value=value))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This interaction is not for you.", ephemeral=True)
            return False
        return True


class ChoiceButton(discord.ui.Button):
    def __init__(self, label: str, value: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.choice_value = value

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, ChoiceView):
            view.value = self.choice_value
            for child in view.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            await interaction.response.edit_message(view=view)
            view.stop()


class AlliedGuildSelect(discord.ui.Select):
    def __init__(self, options: List[str], placeholder: str) -> None:
        select_options = [
            discord.SelectOption(label=opt[:100], value=opt) for opt in options[:25]
        ]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=select_options)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, AlliedGuildSelectView):
            view.value = self.values[0]
            self.disabled = True
            await interaction.response.edit_message(view=view)
            view.stop()


class AlliedGuildSelectView(discord.ui.View):
    def __init__(self, user_id: int, options: List[str], placeholder: str, timeout: int = 180) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.value: Optional[str] = None
        self.add_item(AlliedGuildSelect(options, placeholder))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This interaction is not for you.", ephemeral=True)
            return False
        return True


class MainCharModal(discord.ui.Modal, title="Main Character"):
    char_name = discord.ui.TextInput(label="Main Character Name", max_length=40, required=True)

    def __init__(self) -> None:
        super().__init__()
        self.value: Optional[str] = None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.value = str(self.char_name.value).strip()
        await interaction.response.send_message("Character received.", ephemeral=True)


class MainCharView(discord.ui.View):
    def __init__(self, user_id: int, timeout: int = 300) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.value: Optional[str] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This interaction is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Open Input", style=discord.ButtonStyle.success)
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        modal = MainCharModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        if modal.value:
            self.value = modal.value
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass
            self.stop()


class RulesConfirmView(ChoiceView):
    def __init__(self, user_id: int, label: str, timeout: int = 300) -> None:
        super().__init__(user_id=user_id, options=[(label, "confirmed")], timeout=timeout)


class ManualVerificationDecisionView(discord.ui.View):
    def __init__(
        self,
        member: discord.Member,
        member_role: Optional[discord.Role],
        *,
        char_name: str,
        selected_game: str,
        timeout: int = 172800,
        lang: str = "en-US",
    ) -> None:
        super().__init__(timeout=timeout)
        self.member = member
        self.member_role = member_role
        self.char_name = char_name
        self.selected_game = selected_game
        self.lang = lang
        self.resolved = False
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label == "Bestaetigen":
                    child.label = tr_lang(lang, "Bestaetigen", "Approve")
                elif child.label == "Ablehnen":
                    child.label = tr_lang(lang, "Ablehnen", "Reject")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        actor = interaction.user
        if not isinstance(actor, discord.Member):
            await interaction.response.send_message(
                tr_lang(self.lang, "Nur auf dem Server verfuegbar.", "Only available on the server."),
                ephemeral=True,
            )
            return False
        if actor.guild_permissions.manage_guild or actor.guild_permissions.administrator:
            return True
        await interaction.response.send_message(
            tr_lang(
                self.lang,
                "Keine Berechtigung. Du brauchst 'Server verwalten' oder Administrator.",
                "No permission. You need 'Manage Server' or Administrator.",
            ),
            ephemeral=True,
        )
        return False

    async def _mark_resolved(self, interaction: discord.Interaction, note: str) -> None:
        self.resolved = True
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        try:
            msg = interaction.message
            if msg:
                await msg.edit(content=f"{msg.content}\n{note}", view=self)
        except Exception:
            pass
        self.stop()

    @discord.ui.button(label="Bestaetigen", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.resolved:
            await interaction.response.send_message(
                tr_lang(self.lang, "Bereits bearbeitet.", "Already handled."), ephemeral=True
            )
            return
        if self.member_role is None:
            await interaction.response.send_message(
                tr_lang(
                    self.lang,
                    "Keine Member-Rolle konfiguriert oder Rolle nicht gefunden.",
                    "No member role configured or role not found.",
                ),
                ephemeral=True,
            )
            return
        if self.member_role in self.member.roles:
            await interaction.response.send_message(
                tr_lang(
                    self.lang,
                    f"{self.member.mention} hat die Member-Rolle bereits.",
                    f"{self.member.mention} already has the member role.",
                ),
                ephemeral=True,
            )
            await self._mark_resolved(
                interaction,
                tr_lang(
                    self.lang,
                    f"\n✅ Manuell bestaetigt von {interaction.user.mention} (Rolle war bereits vorhanden).",
                    f"\n✅ Manually approved by {interaction.user.mention} (role was already present).",
                ),
            )
            return
        try:
            await self.member.add_roles(
                self.member_role,
                reason=f"WoW manual verification approved by {interaction.user} ({self.selected_game}/{self.char_name})",
            )
        except Exception as exc:
            await interaction.response.send_message(
                tr_lang(
                    self.lang,
                    f"Rolle konnte nicht gesetzt werden: {exc}",
                    f"Role could not be assigned: {exc}",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            tr_lang(
                self.lang,
                f"Manuell bestaetigt: {self.member.mention} hat jetzt {self.member_role.mention}.",
                f"Manually approved: {self.member.mention} now has {self.member_role.mention}.",
            ),
            ephemeral=True,
        )
        await self._mark_resolved(
            interaction,
            tr_lang(
                self.lang,
                f"\n✅ Manuell bestaetigt von {interaction.user.mention} (Rolle {self.member_role.mention} gesetzt).",
                f"\n✅ Manually approved by {interaction.user.mention} (role {self.member_role.mention} assigned).",
            ),
        )

    @discord.ui.button(label="Ablehnen", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.resolved:
            await interaction.response.send_message(
                tr_lang(self.lang, "Bereits bearbeitet.", "Already handled."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            tr_lang(
                self.lang,
                f"Manuelle Verifizierung fuer {self.member.mention} wurde abgelehnt.",
                f"Manual verification for {self.member.mention} was rejected.",
            ),
            ephemeral=True,
        )
        await self._mark_resolved(
            interaction,
            tr_lang(
                self.lang,
                f"\n❌ Manuelle Verifizierung abgelehnt von {interaction.user.mention}.",
                f"\n❌ Manual verification rejected by {interaction.user.mention}.",
            ),
        )


async def handle_new_member_onboarding(
    bot: commands.Bot,
    member: discord.Member,
    guild_config: dict,
    rank_sync: RankSyncService,
    manual_channel: Optional[discord.TextChannel],
    onboarding_channel: Optional[discord.TextChannel] = None,
    *,
    member_config: Optional[Any] = None,
) -> dict:
    glang = str(guild_config.get("language") or "en-US")
    destination: discord.abc.Messageable
    if onboarding_channel is not None:
        try:
            thread = await onboarding_channel.create_thread(
                name=f"onboarding-{member.display_name[:60]}",
                type=discord.ChannelType.private_thread,
                invitable=False,
                reason="Private onboarding thread",
            )
            await thread.add_user(member)
            await thread.send(
                tr_lang(
                    glang,
                    f"{member.mention} Onboarding gestartet. Nutze die Buttons unten.",
                    f"{member.mention} onboarding started. Use the buttons below.",
                )
            )
            destination = thread
        except Exception:
            destination = await member.create_dm()
    else:
        destination = await member.create_dm()

    onboarding_cfg = guild_config.get("onboarding", {})
    if onboarding_cfg.get("welcome_text_de") or onboarding_cfg.get("welcome_text_en"):
        await destination.send(
            (onboarding_cfg.get("welcome_text_de", "") + "\n" + onboarding_cfg.get("welcome_text_en", "")).strip()
        )

    lang_view = ChoiceView(member.id, [("Deutsch", "de-DE"), ("English (US)", "en-US")], timeout=180)
    await destination.send(TEXTS["de-DE"]["lang_prompt"] + "\n" + TEXTS["en-US"]["lang_prompt"], view=lang_view)
    if await lang_view.wait() or not lang_view.value:
        await destination.send(TEXTS["en-US"]["lang_timeout"])
        return {
            "language": "en-US",
            "selected_game": "retail",
            "registration": {
                "type": "unknown",
                "char_name": "",
                "verified": False,
                "requires_manual_verification": False,
                "rules_confirmed": False,
            },
        }
    lang = lang_view.value
    t = TEXTS[lang]

    role_view = ChoiceView(
        member.id,
        [("Gast" if lang == "de-DE" else "Guest", "guest"), ("Mitglied" if lang == "de-DE" else "Member", "member")],
        timeout=180,
    )
    await destination.send(t["role_prompt"], view=role_view)
    if await role_view.wait() or not role_view.value:
        await destination.send(t["lang_timeout"])
        return {
            "language": lang,
            "selected_game": "retail",
            "registration": {
                "type": "unknown",
                "char_name": "",
                "verified": False,
                "requires_manual_verification": False,
                "rules_confirmed": False,
            },
        }

    roles = guild_config.get("roles", {})
    guest_role = member.guild.get_role(roles.get("guest_role_id", 0))
    allied_role = member.guild.get_role(roles.get("allied_role_id", 0))
    member_role = member.guild.get_role(roles.get("member_role_id", 0))
    rules_cfg = guild_config.get("rules", {})
    rule_channel = member.guild.get_channel(rules_cfg.get("rule_channel_id", 0))
    rule_emoji = str(rules_cfg.get("rule_emoji", "✅") or "✅").strip() or "✅"

    async def confirm_rules() -> bool:
        if rule_channel is not None and isinstance(rule_channel, discord.TextChannel):
            await destination.send(
                t["rules_channel_hint"].format(rules_channel=rule_channel.mention, emoji=rule_emoji)
            )

            def reaction_check(payload: discord.RawReactionActionEvent) -> bool:
                return (
                    payload.guild_id == member.guild.id
                    and payload.channel_id == rule_channel.id
                    and payload.user_id == member.id
                    and str(payload.emoji) == rule_emoji
                )

            try:
                await bot.wait_for("raw_reaction_add", check=reaction_check, timeout=600)
                return True
            except asyncio.TimeoutError:
                return False
            except Exception:
                return False
        confirm_view = RulesConfirmView(member.id, f"{t['rules_confirm']} {rule_emoji}")
        await destination.send(t.get("rules_fallback", "Confirm:"), view=confirm_view)
        return (not await confirm_view.wait()) and confirm_view.value == "confirmed"

    if role_view.value == "guest":
        is_allied = False
        selected_allied_guild = ""
        features = guild_config.get("features", {})
        allied_enabled = features.get("allied_guilds", False)
        allied_guilds = guild_config.get("allied_guilds", [])

        if allied_enabled and allied_guilds:
            guest_type_view = ChoiceView(
                member.id,
                [
                    (t["guest_pure"], "pure"),
                    (t["guest_allied"], "allied"),
                ],
                timeout=180,
            )
            await destination.send(t["guest_type_prompt"], view=guest_type_view)
            if not await guest_type_view.wait() and guest_type_view.value == "allied":
                select_view = AlliedGuildSelectView(
                    member.id,
                    allied_guilds,
                    placeholder=t["allied_guild_select"],
                    timeout=180,
                )
                await destination.send(t["allied_guild_select"], view=select_view)
                if not await select_view.wait() and select_view.value:
                    is_allied = True
                    selected_allied_guild = select_view.value

        if is_allied:
            if allied_role:
                await member.add_roles(allied_role, reason=f"WoW onboarding: allied ({selected_allied_guild})")
            await destination.send(t["allied_done"].format(guild=selected_allied_guild))
        else:
            if guest_role:
                await member.add_roles(guest_role, reason="WoW onboarding: guest")
            await destination.send(t["guest_done"])

        rules_confirmed = await confirm_rules()
        return {
            "language": lang,
            "selected_game": "retail",
            "registration": {
                "type": "allied_member" if is_allied else "guest",
                "char_name": "",
                "allied_guild": selected_allied_guild if is_allied else "",
                "verified": False,
                "requires_manual_verification": False,
                "rules_confirmed": rules_confirmed,
            },
        }

    wow_profiles = guild_config.get("wow_profiles", {})
    if not wow_profiles:
        wow_single = guild_config.get("wow", {})
        wow_profiles = {wow_single.get("version", "retail"): wow_single}
    game_keys = list(wow_profiles.keys())
    selected_game = game_keys[0] if game_keys else "retail"
    game_labels: List[tuple[str, str]] = []
    for k in game_keys:
        gtype = profile_key_to_link_game(k)
        game_labels.append((f"{game_label(gtype)} ({k})", k))
    if not game_labels:
        game_labels = [(game_label(profile_key_to_link_game(selected_game)), selected_game)]
    elif len(game_labels) == 1:
        k0 = game_labels[0][1]
        game_labels = [(game_label(profile_key_to_link_game(k0)), k0)]
    game_view = ChoiceView(member.id, game_labels, timeout=180)
    await destination.send(t["game_prompt"], view=game_view)
    _ = await game_view.wait()
    if game_view.value:
        selected_game = game_view.value

    modal_view = MainCharView(member.id, timeout=300)
    await destination.send(t["mainchar_prompt"], view=modal_view)
    if await modal_view.wait() or not modal_view.value:
        await destination.send(t["mainchar_timeout"])
        return {
            "language": lang,
            "selected_game": selected_game,
            "registration": {
                "type": "member",
                "char_name": "",
                "verified": False,
                "requires_manual_verification": True,
                "rules_confirmed": False,
            },
        }

    main_char = modal_view.value.strip()
    selected_cfg = dict(guild_config)
    selected_cfg["wow"] = wow_profiles.get(selected_game, {})
    rank_title, sync_reason, role_id = await rank_sync.sync_member_rank(
        member,
        selected_cfg,
        main_char,
        profile_key=selected_game,
        previous_bot_role_id=0,
    )
    rank = rank_title if sync_reason == "ok" else None
    if sync_reason == "ok" and rank_title and member_config is not None:
        await merge_rank_sync_game_state(
            member_config,
            selected_game,
            last_title=str(rank_title),
            last_role_id=int(role_id or 0),
        )
    elif sync_reason == "protected" and rank_title and member_config is not None:
        await merge_rank_sync_game_state(
            member_config,
            selected_game,
            last_title=str(rank_title),
        )
    elif sync_reason == "rank_locked" and rank_title and member_config is not None:
        await merge_rank_sync_game_state(
            member_config,
            selected_game,
            last_title=str(rank_title),
        )
    if sync_reason == "protected" and rank_title:
        await send_protected_rank_officer_notice(
            member.guild,
            guild_config,
            member,
            selected_game,
            main_char,
            rank_title,
        )
    if sync_reason == "rank_locked" and rank_title:
        await send_rank_lock_officer_notice(
            member.guild,
            guild_config,
            member,
            selected_game,
            main_char,
            rank_title,
        )
    if rank:
        if member_role and member_role not in member.roles:
            await member.add_roles(member_role, reason="WoW onboarding: verified guild member")
        await destination.send(t["verified"].format(main=main_char, rank=rank))
        if manual_channel:
            await manual_channel.send(
                tr_lang(
                    glang,
                    f"User {member.display_name} ({member.name}) hat sich angemeldet als {main_char}. "
                    f"Spieltyp: {selected_game}. Automatisch verifiziert (Rang: {rank}).",
                    f"User {member.display_name} ({member.name}) signed up as {main_char}. "
                    f"Game type: {selected_game}. Automatically verified (rank: {rank}).",
                )
            )
    elif sync_reason == "rank_locked":
        await destination.send(t["rank_locked_onboarding"])
        if manual_channel:
            await manual_channel.send(
                tr_lang(
                    glang,
                    f"User {member.display_name} ({member.name}): {main_char} ({selected_game}) — "
                    f"Ingame-Rang `{rank_title}` rank-locked (kein Bot-Rang).",
                    f"User {member.display_name} ({member.name}): {main_char} ({selected_game}) — "
                    f"in-game rank `{rank_title}` is rank-locked (no bot role).",
                )
            )
    elif sync_reason == "protected" and rank_title:
        await destination.send(t["protected_rank"])
        if manual_channel:
            await manual_channel.send(
                tr_lang(
                    glang,
                    f"User {member.display_name} ({member.name}): {main_char} ({selected_game}) — "
                    f"Ingame-Rang `{rank_title}` ist geschützt; Offiziere wurden im Rang-Schutz-Kanal benachrichtigt.",
                    f"User {member.display_name} ({member.name}): {main_char} ({selected_game}) — "
                    f"in-game rank `{rank_title}` is protected; officers were notified in the rank-protection channel.",
                )
            )
    else:
        template = guild_config.get("templates", {}).get(
            "manual_verification",
            "Manuelle Verifizierung nötig! User {username} hat sich gemeldet als Char {charname}.",
        )
        if manual_channel:
            await manual_channel.send(template.format(username=member.display_name, charname=main_char))
            await manual_channel.send(
                tr_lang(
                    glang,
                    f"User {member.display_name} ({member.name}) hat sich angemeldet als {main_char}. "
                    f"Spieltyp: {selected_game}. Char nicht gefunden - manuelle Verifizierung noetig.",
                    f"User {member.display_name} ({member.name}) signed up as {main_char}. "
                    f"Game type: {selected_game}. Character not found - manual verification required.",
                )
            )
            await manual_channel.send(
                tr_lang(
                    glang,
                    f"Bitte Entscheidung treffen fuer {member.mention} "
                    f"(Char: `{main_char}`, Spieltyp: `{selected_game}`).",
                    f"Please make a decision for {member.mention} "
                    f"(character: `{main_char}`, game type: `{selected_game}`).",
                ),
                view=ManualVerificationDecisionView(
                    member=member,
                    member_role=member_role,
                    char_name=main_char,
                    selected_game=selected_game,
                    lang=glang,
                ),
            )
        await destination.send(t["manual"])

    rules_confirmed = await confirm_rules()
    if not rules_confirmed:
        await destination.send(t["rules_timeout"])
        return {
            "language": lang,
            "selected_game": selected_game,
            "registration": {
                "type": "member",
                "char_name": main_char,
                "verified": bool(rank),
                "requires_manual_verification": not bool(rank),
                "rules_confirmed": False,
            },
        }

    return {
        "language": lang,
        "selected_game": selected_game,
        "registration": {
            "type": "member",
            "char_name": main_char,
            "verified": bool(rank),
            "requires_manual_verification": not bool(rank),
            "rules_confirmed": rules_confirmed,
        },
    }


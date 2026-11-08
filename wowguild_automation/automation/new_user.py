import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord.ext import commands

from ..character_helpers import (
    GAME_FOREVER,
    game_for_profile,
    game_label,
    merge_rank_sync_game_state,
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

    def __init__(self, forever: bool = False, lang: str = "en-US") -> None:
        super().__init__()
        self.value: Optional[str] = None
        self.forever = forever
        self.surname: Optional[discord.ui.TextInput] = None
        if forever:
            # WoW Forever: first name alone is not unique -> ask for the surname as well.
            self.char_name.label = tr_lang(lang, "Vorname (WoW Forever)", "First name (WoW Forever)")
            self.surname = discord.ui.TextInput(
                label=tr_lang(lang, "Nachname (WoW Forever)", "Surname (WoW Forever)"),
                max_length=40,
                required=True,
            )
            self.add_item(self.surname)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = str(self.char_name.value).strip()
        if self.surname is not None:
            # Stored/compared as "Name Surname"; helpers split it into name + surname.
            name = f"{name} {str(self.surname.value).strip()}".strip()
        self.value = name
        await interaction.response.send_message("Character received.", ephemeral=True)


class MainCharView(discord.ui.View):
    def __init__(self, user_id: int, timeout: int = 300, *, forever: bool = False, lang: str = "en-US") -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.value: Optional[str] = None
        self.forever = forever
        self.lang = lang

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This interaction is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Open Input", style=discord.ButtonStyle.success)
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        modal = MainCharModal(forever=self.forever, lang=self.lang)
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


# --- Persistent manual verification buttons ---------------------------------------
# The custom_id encodes guild and member, so the buttons keep working after a bot restart.
MV_CUSTOM_ID_PREFIX = "wga:mv:"
_MV_PATTERN = r"wga:mv:(?P<action>approve|reject):(?P<guild_id>[0-9]+):(?P<member_id>[0-9]+)"
_MV_RE = re.compile("^" + _MV_PATTERN + "$")
MANUAL_VERIFICATION_USES_DYNAMIC_ITEM = hasattr(discord.ui, "DynamicItem")


def parse_manual_verification_custom_id(custom_id: Any) -> Optional[Tuple[str, int, int]]:
    """'wga:mv:<approve|reject>:<guild_id>:<member_id>' -> (action, guild_id, member_id)."""
    m = _MV_RE.match(str(custom_id or ""))
    if not m:
        return None
    return m.group("action"), int(m.group("guild_id")), int(m.group("member_id"))


def _mv_button(action: str, guild_id: int, member_id: int, lang: str = "en-US") -> discord.ui.Button:
    approve = action == "approve"
    return discord.ui.Button(
        label=tr_lang(lang, "Bestaetigen", "Approve") if approve else tr_lang(lang, "Ablehnen", "Reject"),
        style=discord.ButtonStyle.success if approve else discord.ButtonStyle.danger,
        emoji="\u2705" if approve else "\u274c",
        custom_id=f"{MV_CUSTOM_ID_PREFIX}{action}:{int(guild_id)}:{int(member_id)}",
    )


async def _mv_reply(interaction: discord.Interaction, text: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass


async def handle_manual_verification_click(
    cog: Any,
    interaction: discord.Interaction,
    action: str,
    guild_id: int,
    member_id: int,
) -> None:
    """Approve / reject a pending manual verification. Permission is checked on every click."""
    guild = interaction.guild
    if cog is None or guild is None or guild.id != int(guild_id):
        await _mv_reply(interaction, "Dieser Button ist nicht mehr gueltig. / This button is no longer valid.")
        return
    lang = await cog._guild_lang(guild)
    actor = interaction.user
    if not isinstance(actor, discord.Member) or not (
        actor.guild_permissions.manage_guild or actor.guild_permissions.administrator
    ):
        await _mv_reply(
            interaction,
            tr_lang(
                lang,
                "Keine Berechtigung. Du brauchst 'Server verwalten' oder Administrator.",
                "No permission. You need 'Manage Server' or Administrator.",
            ),
        )
        return
    member = guild.get_member(int(member_id))
    if member is None:
        await _mv_reply(
            interaction,
            tr_lang(lang, "Mitglied ist nicht (mehr) auf dem Server.", "Member is no longer on the server."),
        )
        return
    cfg = await cog._guild_config(guild)
    member_role = guild.get_role(int((cfg.get("roles") or {}).get("member_role_id", 0) or 0))
    conf = cog.config.member(member)
    async with cog._member_lock(guild.id, member.id):
        session = str(await conf.onboarding_session_id() or "")
        reg_raw = await conf.registration()
        reg = dict(reg_raw) if isinstance(reg_raw, dict) else {}
        if reg.get("manual_review") in ("approved", "rejected") and str(reg.get("manual_review_session") or "") == session:
            await _mv_reply(interaction, tr_lang(lang, "Bereits bearbeitet.", "Already handled."))
            return
        if action == "approve":
            if member_role is None:
                await _mv_reply(
                    interaction,
                    tr_lang(
                        lang,
                        "Keine Member-Rolle konfiguriert oder Rolle nicht gefunden.",
                        "No member role configured or role not found.",
                    ),
                )
                return
            if member_role not in member.roles:
                try:
                    await member.add_roles(
                        member_role,
                        reason=f"WoW manual verification approved by {actor} ({actor.id})",
                    )
                except discord.Forbidden:
                    await _mv_reply(
                        interaction,
                        tr_lang(
                            lang,
                            "Der Bot darf die Member-Rolle nicht setzen (Rechte / Rollen-Reihenfolge).",
                            "The bot may not assign the member role (permissions / role order).",
                        ),
                    )
                    return
                except discord.HTTPException as exc:
                    await _mv_reply(
                        interaction,
                        tr_lang(lang, f"Rolle konnte nicht gesetzt werden: {exc}", f"Role could not be assigned: {exc}"),
                    )
                    return
            reg["verified"] = True
            reg["requires_manual_verification"] = False
            note = tr_lang(
                lang,
                f"\u2705 Manuell bestaetigt von {actor.mention} (Rolle {member_role.mention}).",
                f"\u2705 Manually approved by {actor.mention} (role {member_role.mention}).",
            )
        else:
            note = tr_lang(
                lang,
                f"\u274c Manuelle Verifizierung abgelehnt von {actor.mention}.",
                f"\u274c Manual verification rejected by {actor.mention}.",
            )
        reg["manual_review"] = "approved" if action == "approve" else "rejected"
        reg["manual_review_by"] = int(actor.id)
        reg["manual_review_session"] = session
        await conf.registration.set(reg)
    content = (interaction.message.content if interaction.message else "") or ""
    new_content = content[: max(0, 2000 - len(note) - 1)] + "\n" + note
    try:
        await interaction.response.edit_message(content=new_content, view=None)
    except discord.HTTPException:
        await _mv_reply(interaction, note)


if MANUAL_VERIFICATION_USES_DYNAMIC_ITEM:

    class ManualVerificationButton(discord.ui.DynamicItem[discord.ui.Button], template=_MV_PATTERN):
        """Persistent approve/reject button (discord.py >= 2.4); all state lives in the custom_id."""

        def __init__(self, action: str, guild_id: int, member_id: int, lang: str = "en-US") -> None:
            super().__init__(_mv_button(action, guild_id, member_id, lang))
            self.action = action
            self.guild_id = int(guild_id)
            self.member_id = int(member_id)

        @classmethod
        async def from_custom_id(
            cls, interaction: discord.Interaction, item: discord.ui.Button, match: "re.Match[str]"
        ) -> "ManualVerificationButton":
            return cls(match["action"], int(match["guild_id"]), int(match["member_id"]))

        async def callback(self, interaction: discord.Interaction) -> None:
            cog = interaction.client.get_cog("WowGuildAutomation")
            await handle_manual_verification_click(cog, interaction, self.action, self.guild_id, self.member_id)

else:
    ManualVerificationButton = None  # type: ignore[assignment,misc]


def build_manual_verification_view(guild_id: int, member_id: int, lang: str = "en-US") -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in ("approve", "reject"):
        if ManualVerificationButton is not None:
            view.add_item(ManualVerificationButton(action, guild_id, member_id, lang))
        else:
            view.add_item(_mv_button(action, guild_id, member_id, lang))
    return view


async def send_manual_verification_prompt(
    channel: discord.abc.Messageable,
    content: str,
    guild_id: int,
    member_id: int,
    lang: str = "en-US",
) -> discord.Message:
    view = build_manual_verification_view(guild_id, member_id, lang)
    msg = await channel.send(content, view=view)
    if ManualVerificationButton is None:
        # Fallback (discord.py < 2.4): clicks are handled by the cog's on_interaction listener,
        # so the in-memory view must not dispatch as well.
        view.stop()
    return msg


async def _safe_add_role(member: discord.Member, role: Optional[discord.Role], reason: str) -> bool:
    """Adds a role; returns False instead of raising on Forbidden / HTTP errors."""
    if role is None or role in member.roles:
        return True
    try:
        await member.add_roles(role, reason=reason)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def handle_new_member_onboarding(
    bot: commands.Bot,
    member: discord.Member,
    guild_config: dict,
    rank_sync: RankSyncService,
    manual_channel: Optional[discord.TextChannel],
    onboarding_channel: Optional[discord.TextChannel] = None,
    *,
    member_config: Optional[Any] = None,
    member_lock: Optional[asyncio.Lock] = None,
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
            await _safe_add_role(member, allied_role, f"WoW onboarding: allied ({selected_allied_guild})")
            await destination.send(t["allied_done"].format(guild=selected_allied_guild))
        else:
            await _safe_add_role(member, guest_role, "WoW onboarding: guest")
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
        gtype = game_for_profile(k, wow_profiles.get(k))
        game_labels.append((f"{game_label(gtype)} ({k})", k))
    if not game_labels:
        game_labels = [(game_label(game_for_profile(selected_game, None)), selected_game)]
    elif len(game_labels) == 1:
        k0 = game_labels[0][1]
        game_labels = [(game_label(game_for_profile(k0, wow_profiles.get(k0))), k0)]
    game_view = ChoiceView(member.id, game_labels, timeout=180)
    await destination.send(t["game_prompt"], view=game_view)
    _ = await game_view.wait()
    if game_view.value:
        selected_game = game_view.value

    sel_prof = wow_profiles.get(selected_game, {})
    # Game type comes from the profile version (profile key only as fallback).
    link_game = game_for_profile(selected_game, sel_prof)
    forever_game = link_game == GAME_FOREVER
    modal_view = MainCharView(member.id, timeout=300, forever=forever_game, lang=lang)
    mainchar_prompt = t["mainchar_prompt"]
    if forever_game:
        mainchar_prompt += tr_lang(
            lang,
            " WoW Forever: bitte Vor- **und** Nachname angeben.",
            " WoW Forever: please enter first name **and** surname.",
        )
    await destination.send(mainchar_prompt, view=modal_view)
    if await modal_view.wait() or not modal_view.value:
        await destination.send(t["mainchar_timeout"])
        return {
            "language": lang,
            "selected_game": selected_game,
            "link_game": link_game,
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
    # Protected / rank-lock checks happen inside plan_sync BEFORE any role is assigned.
    async with (member_lock or asyncio.Lock()):
        rank_title, sync_reason, role_id = await rank_sync.sync_member_rank(
            member,
            selected_cfg,
            main_char,
            profile_key=selected_game,
            previous_bot_role_id=0,
        )
        if sync_reason == "ok" and rank_title and member_config is not None:
            await merge_rank_sync_game_state(
                member_config,
                link_game,
                last_title=str(rank_title),
                last_role_id=int(role_id or 0),
            )
        elif sync_reason in ("protected", "rank_locked") and rank_title and member_config is not None:
            await merge_rank_sync_game_state(
                member_config,
                link_game,
                last_title=str(rank_title),
            )
    rank = rank_title if sync_reason == "ok" else None
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
        role_ok = await _safe_add_role(member, member_role, "WoW onboarding: verified guild member")
        await destination.send(t["verified"].format(main=main_char, rank=rank))
        if not role_ok and manual_channel:
            await manual_channel.send(
                tr_lang(
                    glang,
                    f"Member-Rolle konnte {member.mention} nicht gegeben werden (Bot-Rechte / Rollen-Reihenfolge).",
                    f"Member role could not be given to {member.mention} (bot permissions / role order).",
                )
            )
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
            await send_manual_verification_prompt(
                manual_channel,
                tr_lang(
                    glang,
                    f"Bitte Entscheidung treffen fuer {member.mention} "
                    f"(Char: `{main_char}`, Spieltyp: `{selected_game}`).",
                    f"Please make a decision for {member.mention} "
                    f"(character: `{main_char}`, game type: `{selected_game}`).",
                ),
                member.guild.id,
                member.id,
                lang=glang,
            )
        await destination.send(t["manual"])

    rules_confirmed = await confirm_rules()
    if not rules_confirmed:
        await destination.send(t["rules_timeout"])
        return {
            "language": lang,
            "selected_game": selected_game,
            "link_game": link_game,
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
        "link_game": link_game,
        "registration": {
            "type": "member",
            "char_name": main_char,
            "verified": bool(rank),
            "requires_manual_verification": not bool(rank),
            "rules_confirmed": rules_confirmed,
        },
    }


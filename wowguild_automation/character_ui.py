"""Interactive discord.ui flows for linking guild roster characters (Retail / MoP Classic)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Set, Tuple

import discord

from .pdc_dashboard import tr_lang
from .character_helpers import (
    GAME_MOP,
    GAME_RETAIL,
    SUPPORTED_GAMES,
    char_tuple_key,
    clear_main_for_game,
    game_label,
    get_linked_list,
    get_main_characters,
    set_linked_list,
    set_main_for_game,
    wow_profile_for_game,
)

if TYPE_CHECKING:
    from .wowguild_automation import WowGuildAutomation

PANEL_INTRO = (
    "Verknüpfe nur Charaktere, die auf eurer **Gildenroster**-API stehen.\n"
    "Alles läuft in **dieser einen** Nachricht (ephemeral)."
)

ADMIN_PANEL_INTRO = (
    "**Officer-Panel**\n"
    "Wähle ein **Mitglied** (Dropdown), um dessen Chars zu verwalten, oder nutze die Listen-Buttons.\n\n"
    "Nach Auswahl: gleiche Aktionen wie der User — inkl. Rang-Sync für genau dieses Mitglied."
)

def _panel_intro(lang: str) -> str:
    return tr_lang(
        lang,
        PANEL_INTRO,
        (
            "Only link characters that appear on your **guild roster** API.\n"
            "Everything runs in **this single** message (ephemeral)."
        ),
    )


def _admin_panel_intro(lang: str) -> str:
    return tr_lang(
        lang,
        ADMIN_PANEL_INTRO,
        (
            "**Officer panel**\n"
            "Pick a **member** (dropdown) to manage their chars, or use the list buttons.\n\n"
            "After selecting: same actions as the user — including rank sync for exactly this member."
        ),
    )


LINKED_PAGE_SIZE = 24
# Discord: message content max. 2000 characters — otherwise edit_message fails ("Interaction failed").
PANEL_MESSAGE_CONTENT_LIMIT = 2000


def _truncate_for_message(body: str, footer: str, limit: int = PANEL_MESSAGE_CONTENT_LIMIT) -> str:
    """body + footer ≤ limit (footer is preserved)."""
    max_body = limit - len(footer)
    if max_body < 80:
        return (body[: max(40, limit - 40)] + "…")[:limit]
    if len(body) <= max_body:
        return body + footer
    return body[: max_body - 25].rstrip() + "\n… *(Liste gekürzt — `/wow-user` → list-my)*" + footer


def _menu_view(
    cog: "WowGuildAutomation",
    guild: discord.Guild,
    member: discord.Member,
    *,
    actor: Optional[discord.Member] = None,
    lang: str = "en-US",
) -> CharMainMenuView:
    return CharMainMenuView(cog, guild, member, actor=actor, lang=lang)


class CharMainMenuView(discord.ui.View):
    def __init__(
        self,
        cog: "WowGuildAutomation",
        guild: discord.Guild,
        member: discord.Member,
        *,
        actor: Optional[discord.Member] = None,
        lang: str = "en-US",
    ) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.guild = guild
        self.member = member
        self.actor = actor if actor is not None else member
        self.lang = lang
        self.add_btn.label = tr_lang(lang, "Chars hinzufügen", "Add characters")
        self.main_btn.label = tr_lang(lang, "Main setzen", "Set main")
        self.list_btn.label = tr_lang(lang, "Meine Chars", "My characters")
        self.remove_btn.label = tr_lang(lang, "Chars entfernen", "Remove characters")
        attach_officer_extras_if_needed(self)

    @discord.ui.button(label="Chars hinzufügen", style=discord.ButtonStyle.primary, row=0)
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        await interaction.response.edit_message(
            content=tr_lang(glang, "Welches Spiel?", "Which game?"),
            view=GamePickView(self.cog, self.guild, self.member, mode="add", actor=self.actor, lang=glang),
        )

    @discord.ui.button(label="Main setzen", style=discord.ButtonStyle.secondary, row=0)
    async def main_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        linked = await get_linked_list(self.cog.config.member(self.member))
        if not linked:
            await interaction.response.edit_message(
                content=tr_lang(glang, "Noch keine Chars verknüpft.", "No characters linked yet."),
                view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
            )
            return
        await interaction.response.edit_message(
            content=tr_lang(glang, "**Main pro Spiel** — zuerst Version wählen:", "**Main per game** — pick the version first:"),
            view=MainGamePickView(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
        )

    @discord.ui.button(label="Meine Chars", style=discord.ButtonStyle.secondary, row=1)
    async def list_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            text = await self.cog._format_user_char_list_ephemeral(
                self.guild, self.member, header_user=False
            )
        except Exception:
            await interaction.edit_original_response(
                content=tr_lang(glang, "Char-Liste konnte nicht geladen werden. Nutze `/wow-user` → list-my.", "Character list could not be loaded. Use `/wow-user` → list-my."),
                view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
            )
            return
        footer = "\n\n—\n" + _panel_intro(glang)
        full = _truncate_for_message(text, footer)
        try:
            await interaction.edit_original_response(
                content=full,
                view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
            )
        except discord.HTTPException:
            try:
                short = text[:1900] + ("…" if len(text) > 1900 else "")
                await interaction.followup.send(short, ephemeral=True)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Chars entfernen", style=discord.ButtonStyle.danger, row=1)
    async def remove_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        linked = await get_linked_list(self.cog.config.member(self.member))
        if not linked:
            await interaction.response.edit_message(
                content=tr_lang(glang, "Nichts zum Entfernen.", "Nothing to remove."),
                view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
            )
            return
        ordered = sorted(linked, key=lambda e: (e["game_type"], e["name"].lower()))
        await interaction.response.edit_message(
            content=self._remove_caption(ordered, 0, glang),
            view=LinkedRemovePageView(
                self.cog, self.guild, self.actor, ordered, page=0, officer_mode=False, lang=glang
            ),
        )

    @staticmethod
    def _remove_caption(ordered: List[Dict[str, str]], page: int, lang: str = "de-DE") -> str:
        total_pages = max(1, (len(ordered) + LINKED_PAGE_SIZE - 1) // LINKED_PAGE_SIZE)
        return tr_lang(
            lang,
            (
                f"**Chars entfernen** — Seite **{page + 1}/{total_pages}** "
                f"({len(ordered)} gesamt). Wähle im Dropdown (max. {LINKED_PAGE_SIZE} pro Seite)."
            ),
            (
                f"**Remove characters** — page **{page + 1}/{total_pages}** "
                f"({len(ordered)} total). Pick in the dropdown (max. {LINKED_PAGE_SIZE} per page)."
            ),
        )


class GamePickView(discord.ui.View):
    def __init__(
        self,
        cog: "WowGuildAutomation",
        guild: discord.Guild,
        member: discord.Member,
        *,
        mode: str,
        actor: Optional[discord.Member] = None,
        lang: str = "en-US",
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.member = member
        self.mode = mode
        self.actor = actor if actor is not None else member
        self.lang = lang
        self.back.label = tr_lang(lang, "◀ Menü", "◀ Menu")

    @discord.ui.button(label="◀ Menü", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        await interaction.response.edit_message(
            content=_panel_intro(glang),
            view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
        )

    @discord.ui.button(label="Retail", style=discord.ButtonStyle.primary, row=0)
    async def retail(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_roster(interaction, GAME_RETAIL)

    @discord.ui.button(label="MoP Classic", style=discord.ButtonStyle.primary, row=0)
    async def mop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_roster(interaction, GAME_MOP)

    async def _open_roster(self, interaction: discord.Interaction, game: str) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        cfg = await self.cog._guild_config(self.guild)
        prof = await wow_profile_for_game(cfg, game)
        if not prof or not prof.get("realm") or not prof.get("guild_name"):
            await interaction.response.edit_message(
                content=tr_lang(
                    glang,
                    (
                        f"Für **{game_label(game)}** fehlen Realm/Gildenname im Server-Setup "
                        "(Web-Dashboard / `wow guildsettings`)."
                    ),
                    (
                        f"Realm/guild name missing for **{game_label(game)}** in the server setup "
                        "(web dashboard / `wow guildsettings`)."
                    ),
                ),
                view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
            )
            return
        names = await self.cog.blizzard.roster_character_names(
            prof.get("region", "eu"),
            prof.get("version", game),
            prof.get("realm", ""),
            prof.get("guild_name", ""),
        )
        if not names:
            await interaction.response.edit_message(
                content=tr_lang(glang, "Gildenroster leer oder API-Fehler. Prüfe Client-ID/Secret und Profil.", "Guild roster empty or API error. Check client ID/secret and profile."),
                view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
            )
            return
        total_pages = max(1, (len(names) + LINKED_PAGE_SIZE - 1) // LINKED_PAGE_SIZE)
        await interaction.response.edit_message(
            content=tr_lang(
                glang,
                (
                    f"Roster **{game_label(game)}** — Seite **1/{total_pages}**. "
                    "Mehrfachauswahl im Dropdown bestätigt den Eintrag."
                ),
                (
                    f"Roster **{game_label(game)}** — page **1/{total_pages}**. "
                    "Multi-select in the dropdown confirms the entry."
                ),
            ),
            view=RosterPageView(
                self.cog, self.guild, self.member, game, names, page=0, actor=self.actor, lang=glang
            ),
        )


class RosterPageView(discord.ui.View):
    def __init__(
        self,
        cog: "WowGuildAutomation",
        guild: discord.Guild,
        member: discord.Member,
        game_type: str,
        all_names: List[str],
        page: int,
        *,
        actor: Optional[discord.Member] = None,
        lang: str = "en-US",
    ) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.guild = guild
        self.member = member
        self.actor = actor if actor is not None else member
        self.lang = lang
        self.game_type = game_type
        self.all_names = all_names
        self.page = max(0, page)
        start = self.page * LINKED_PAGE_SIZE
        chunk = all_names[start : start + LINKED_PAGE_SIZE]
        options: List[discord.SelectOption] = []
        for n in chunk[:25]:
            options.append(discord.SelectOption(label=n[:100], value=n[:100]))
        if options:

            async def _select_cb(interaction: discord.Interaction) -> None:
                await RosterPageView._handle_roster_select(
                    interaction,
                    cog,
                    guild,
                    member,
                    game_type,
                    all_names,
                    page,
                    self,
                    self.actor,
                )

            select = discord.ui.Select(
                placeholder=tr_lang(self.lang, "Charaktere wählen → Auswahl übernimmt", "Pick characters → selection applies"),
                min_values=1,
                max_values=len(options),
                options=options,
            )
            select.callback = _select_cb
            self.add_item(select)
        b_back = discord.ui.Button(label=tr_lang(self.lang, "◀ Menü", "◀ Menu"), style=discord.ButtonStyle.secondary, row=2)
        b_back.callback = self._back_menu
        self.add_item(b_back)
        if self.page > 0:
            b = discord.ui.Button(label=tr_lang(self.lang, "◀ Seite", "◀ Page"), style=discord.ButtonStyle.secondary, row=1)
            b.callback = self._prev_page
            self.add_item(b)
        if start + LINKED_PAGE_SIZE < len(all_names):
            b2 = discord.ui.Button(label=tr_lang(self.lang, "Seite ▶", "Page ▶"), style=discord.ButtonStyle.secondary, row=1)
            b2.callback = self._next_page
            self.add_item(b2)

    async def _back_menu(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(
            content=_panel_intro(glang),
            view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
        )

    async def _prev_page(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        self.stop()
        tp = max(1, (len(self.all_names) + LINKED_PAGE_SIZE - 1) // LINKED_PAGE_SIZE)
        new_page = self.page - 1
        await interaction.response.edit_message(
            content=tr_lang(
                glang,
                f"Roster **{game_label(self.game_type)}** — Seite **{new_page + 1}/{tp}**.",
                f"Roster **{game_label(self.game_type)}** — page **{new_page + 1}/{tp}**.",
            ),
            view=RosterPageView(
                self.cog,
                self.guild,
                self.member,
                self.game_type,
                self.all_names,
                new_page,
                actor=self.actor,
                lang=glang,
            ),
        )

    async def _next_page(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        self.stop()
        tp = max(1, (len(self.all_names) + LINKED_PAGE_SIZE - 1) // LINKED_PAGE_SIZE)
        new_page = self.page + 1
        await interaction.response.edit_message(
            content=tr_lang(
                glang,
                f"Roster **{game_label(self.game_type)}** — Seite **{new_page + 1}/{tp}**.",
                f"Roster **{game_label(self.game_type)}** — page **{new_page + 1}/{tp}**.",
            ),
            view=RosterPageView(
                self.cog,
                self.guild,
                self.member,
                self.game_type,
                self.all_names,
                new_page,
                actor=self.actor,
                lang=glang,
            ),
        )

    @staticmethod
    async def _handle_roster_select(
        interaction: discord.Interaction,
        cog: "WowGuildAutomation",
        guild: discord.Guild,
        member: discord.Member,
        game_type: str,
        all_names: List[str],
        page: int,
        view: "RosterPageView",
        actor: discord.Member,
    ) -> None:
        glang = await cog._guild_lang(guild)
        if interaction.user.id != actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        selected = interaction.data.get("values") or []
        if not selected:
            await interaction.response.send_message(tr_lang(glang, "Nichts gewählt.", "Nothing selected."), ephemeral=True)
            return
        msg, ok = await cog._try_add_characters_for_member(guild, member, game_type, list(selected))
        view.stop()
        await interaction.response.edit_message(
            content=f"{msg}\n\n{_panel_intro(glang)}",
            view=_menu_view(cog, guild, member, actor=actor, lang=glang),
        )


class MainGamePickView(discord.ui.View):
    def __init__(
        self,
        cog: "WowGuildAutomation",
        guild: discord.Guild,
        member: discord.Member,
        *,
        actor: Optional[discord.Member] = None,
        lang: str = "en-US",
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.member = member
        self.actor = actor if actor is not None else member
        self.lang = lang
        self.back.label = tr_lang(lang, "◀ Menü", "◀ Menu")

    @discord.ui.button(label="◀ Menü", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        await interaction.response.edit_message(
            content=_panel_intro(glang),
            view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
        )

    @discord.ui.button(label="Retail", style=discord.ButtonStyle.primary, row=0)
    async def retail(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open(interaction, GAME_RETAIL)

    @discord.ui.button(label="MoP Classic", style=discord.ButtonStyle.primary, row=0)
    async def mop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open(interaction, GAME_MOP)

    async def _open(self, interaction: discord.Interaction, game: str) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        linked = await get_linked_list(self.cog.config.member(self.member))
        subset = [e for e in linked if e["game_type"] == game]
        if not subset:
            await interaction.response.edit_message(
                content=tr_lang(glang, f"Keine verknüpften Chars für **{game_label(game)}**.", f"No linked characters for **{game_label(game)}**."),
                view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
            )
            return
        ordered = sorted(subset, key=lambda e: e["name"].lower())
        tp = max(1, (len(ordered) + LINKED_PAGE_SIZE - 1) // LINKED_PAGE_SIZE)
        await interaction.response.edit_message(
            content=tr_lang(
                glang,
                (
                    f"**Main für {game_label(game)}** — Seite **1/{tp}**. "
                    "Oder „Nach Namen suchen“."
                ),
                (
                    f"**Main for {game_label(game)}** — page **1/{tp}**. "
                    "Or “search by name”."
                ),
            ),
            view=LinkedMainPageView(
                self.cog, self.guild, self.member, game, ordered, page=0, actor=self.actor, lang=glang
            ),
        )


class LinkedMainPageView(discord.ui.View):
    def __init__(
        self,
        cog: "WowGuildAutomation",
        guild: discord.Guild,
        member: discord.Member,
        game_type: str,
        ordered: List[Dict[str, str]],
        page: int,
        *,
        actor: Optional[discord.Member] = None,
        lang: str = "en-US",
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.member = member
        self.actor = actor if actor is not None else member
        self.lang = lang
        self.game_type = game_type
        self.ordered = ordered
        self.page = max(0, page)
        self._main_pick_map: Dict[str, Tuple[str, str]] = {}
        start = self.page * LINKED_PAGE_SIZE
        chunk = ordered[start : start + LINKED_PAGE_SIZE]
        opts: List[discord.SelectOption] = []
        for i, e in enumerate(chunk[:25]):
            tok = f"m{self.page}_{i}"
            self._main_pick_map[tok] = (e["name"], e["game_type"])
            label = f"{e['name']}"[:100]
            opts.append(discord.SelectOption(label=label, value=tok))
        if opts:
            s = discord.ui.Select(placeholder=tr_lang(self.lang, "Main-Char wählen", "Pick main character"), min_values=1, max_values=1, options=opts)
            s.callback = self._pick
            self.add_item(s)
        row_nav = 1
        if self.page > 0:
            b = discord.ui.Button(label=tr_lang(self.lang, "◀ Seite", "◀ Page"), style=discord.ButtonStyle.secondary, row=row_nav)
            b.callback = self._prev
            self.add_item(b)
        if start + LINKED_PAGE_SIZE < len(ordered):
            b2 = discord.ui.Button(label=tr_lang(self.lang, "Seite ▶", "Page ▶"), style=discord.ButtonStyle.secondary, row=row_nav)
            b2.callback = self._next
            self.add_item(b2)
        b_menu = discord.ui.Button(label=tr_lang(self.lang, "◀ Menü", "◀ Menu"), style=discord.ButtonStyle.secondary, row=2)
        b_menu.callback = self._back_menu
        self.add_item(b_menu)
        b_search = discord.ui.Button(label=tr_lang(self.lang, "Nach Namen suchen", "Search by name"), style=discord.ButtonStyle.secondary, row=2)
        b_search.callback = self._search
        self.add_item(b_search)

    def _caption(self, lang: str = "de-DE") -> str:
        tp = max(1, (len(self.ordered) + LINKED_PAGE_SIZE - 1) // LINKED_PAGE_SIZE)
        return tr_lang(
            lang,
            f"**Main für {game_label(self.game_type)}** — Seite **{self.page + 1}/{tp}**.",
            f"**Main for {game_label(self.game_type)}** — page **{self.page + 1}/{tp}**.",
        )

    async def _back_menu(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        await interaction.response.edit_message(
            content=_panel_intro(glang),
            view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
        )

    async def _search(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        await interaction.response.send_modal(
            MainCharSearchModal(self.cog, self.guild, self.member, self.game_type, actor=self.actor, lang=glang)
        )

    async def _prev(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        np = self.page - 1
        nv = LinkedMainPageView(
            self.cog, self.guild, self.member, self.game_type, self.ordered, np, actor=self.actor, lang=glang
        )
        await interaction.response.edit_message(content=nv._caption(glang), view=nv)

    async def _next(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        np = self.page + 1
        nv = LinkedMainPageView(
            self.cog, self.guild, self.member, self.game_type, self.ordered, np, actor=self.actor, lang=glang
        )
        await interaction.response.edit_message(content=nv._caption(glang), view=nv)

    async def _pick(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        tok = (interaction.data.get("values") or [""])[0]
        pair = self._main_pick_map.get(tok)
        if not pair:
            await interaction.response.send_message(tr_lang(glang, "Ungültig.", "Invalid."), ephemeral=True)
            return
        name, game = pair[0], pair[1]
        if game not in SUPPORTED_GAMES:
            game = GAME_RETAIL
        await set_main_for_game(self.cog.config.member(self.member), game, name.strip())
        await self.cog.config.member(self.member).selected_game.set(game)
        asyncio.create_task(
            self.cog._schedule_rank_sync_after_main(self.guild, self.member, game, name.strip())
        )
        self.stop()
        await interaction.response.edit_message(
            content=tr_lang(
                glang,
                f"Main **{game_label(game)}** gesetzt: **{name.strip()}**.\n\n{_panel_intro(glang)}",
                f"Main **{game_label(game)}** set: **{name.strip()}**.\n\n{_panel_intro(glang)}",
            ),
            view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
        )


class MainCharSearchModal(discord.ui.Modal, title="Charakter suchen"):
    query = discord.ui.TextInput(
        label="Name (Teilstring, Groß/Klein egal)",
        placeholder="z.B. ann",
        max_length=32,
        required=True,
    )

    def __init__(
        self,
        cog: "WowGuildAutomation",
        guild: discord.Guild,
        member: discord.Member,
        game_type: str,
        *,
        actor: Optional[discord.Member] = None,
        lang: str = "en-US",
    ) -> None:
        super().__init__(title=tr_lang(lang, "Charakter suchen", "Search character"))
        self.cog = cog
        self.guild = guild
        self.member = member
        self.game_type = game_type
        self.actor = actor if actor is not None else member
        self.lang = lang
        self.query.label = tr_lang(lang, "Name (Teilstring, Groß/Klein egal)", "Name (substring, case-insensitive)")
        self.query.placeholder = tr_lang(lang, "z.B. ann", "e.g. ann")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        q = str(self.query.value).strip().lower()
        linked = await get_linked_list(self.cog.config.member(self.member))
        subset = [e for e in linked if e["game_type"] == self.game_type and q in e["name"].lower()]
        if not subset:
            await interaction.response.edit_message(
                content=tr_lang(
                    glang,
                    f"Kein Treffer für „{self.query.value}“ in **{game_label(self.game_type)}**.\n\n{_panel_intro(glang)}",
                    f"No match for “{self.query.value}” in **{game_label(self.game_type)}**.\n\n{_panel_intro(glang)}",
                ),
                view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
            )
            return
        if len(subset) == 1:
            e = subset[0]
            await set_main_for_game(self.cog.config.member(self.member), e["game_type"], e["name"])
            await self.cog.config.member(self.member).selected_game.set(e["game_type"])
            asyncio.create_task(
                self.cog._schedule_rank_sync_after_main(
                    self.guild, self.member, e["game_type"], e["name"]
                )
            )
            await interaction.response.edit_message(
                content=tr_lang(
                    glang,
                    f"Main **{game_label(e['game_type'])}** gesetzt: **{e['name']}**.\n\n{_panel_intro(glang)}",
                    f"Main **{game_label(e['game_type'])}** set: **{e['name']}**.\n\n{_panel_intro(glang)}",
                ),
                view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
            )
            return
        ordered = sorted(subset, key=lambda e: e["name"].lower())[:25]
        view = MainSearchDisambigView(self.cog, self.guild, self.member, ordered, actor=self.actor, lang=glang)
        await interaction.response.edit_message(
            content=tr_lang(glang, "Mehrere Treffer — bitte einen wählen:", "Multiple matches — please pick one:"),
            view=view,
        )


class MainSearchDisambigView(discord.ui.View):
    def __init__(
        self,
        cog: "WowGuildAutomation",
        guild: discord.Guild,
        member: discord.Member,
        entries: List[Dict[str, str]],
        *,
        actor: Optional[discord.Member] = None,
        lang: str = "en-US",
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.member = member
        self.actor = actor if actor is not None else member
        self.lang = lang
        self._pick_map: Dict[str, Tuple[str, str]] = {}
        opts: List[discord.SelectOption] = []
        for i, e in enumerate(entries[:25]):
            tok = f"d{i}"
            self._pick_map[tok] = (e["name"], e["game_type"])
            opts.append(discord.SelectOption(label=e["name"][:100], value=tok))
        s = discord.ui.Select(placeholder=tr_lang(self.lang, "Main wählen", "Pick main"), min_values=1, max_values=1, options=opts)
        s.callback = self._pick
        self.add_item(s)
        b = discord.ui.Button(label=tr_lang(self.lang, "◀ Menü", "◀ Menu"), style=discord.ButtonStyle.secondary, row=1)
        b.callback = self._menu
        self.add_item(b)

    async def _menu(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        await interaction.response.edit_message(
            content=_panel_intro(glang),
            view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
        )

    async def _pick(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        tok = (interaction.data.get("values") or [""])[0]
        pair = self._pick_map.get(tok)
        if not pair:
            await interaction.response.send_message(tr_lang(glang, "Ungültig.", "Invalid."), ephemeral=True)
            return
        name, game = pair[0], pair[1]
        if game not in SUPPORTED_GAMES:
            game = GAME_RETAIL
        await set_main_for_game(self.cog.config.member(self.member), game, name.strip())
        await self.cog.config.member(self.member).selected_game.set(game)
        asyncio.create_task(
            self.cog._schedule_rank_sync_after_main(self.guild, self.member, game, name.strip())
        )
        await interaction.response.edit_message(
            content=tr_lang(
                glang,
                f"Main **{game_label(game)}** gesetzt: **{name.strip()}**.\n\n{_panel_intro(glang)}",
                f"Main **{game_label(game)}** set: **{name.strip()}**.\n\n{_panel_intro(glang)}",
            ),
            view=_menu_view(self.cog, self.guild, self.member, actor=self.actor, lang=glang),
        )


class LinkedRemovePageView(discord.ui.View):
    """Remove linked chars; paged multi-select. officer_mode uses officer/target."""

    def __init__(
        self,
        cog: "WowGuildAutomation",
        guild: discord.Guild,
        actor: discord.Member,
        ordered: List[Dict[str, str]],
        page: int,
        *,
        officer_mode: bool,
        officer: Optional[discord.Member] = None,
        target: Optional[discord.Member] = None,
        accumulated: Optional[Set[Tuple[str, str]]] = None,
        lang: str = "en-US",
    ) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.guild = guild
        self.actor = actor
        self.lang = lang
        self.ordered = ordered
        self.page = max(0, page)
        self.officer_mode = officer_mode
        self.officer = officer
        self.target = target
        self.accumulated: Set[Tuple[str, str]] = accumulated or set()
        self._remove_token_map: Dict[str, Tuple[str, str]] = {}
        start = self.page * LINKED_PAGE_SIZE
        chunk = ordered[start : start + LINKED_PAGE_SIZE]
        opts: List[discord.SelectOption] = []
        for i, e in enumerate(chunk[:25]):
            tok = f"rm{self.page}_{i}"
            self._remove_token_map[tok] = (e["name"], e["game_type"])
            label = f"{e['name']} ({game_label(e['game_type'])})"[:100]
            opts.append(discord.SelectOption(label=label, value=tok))
        if opts:
            s = discord.ui.Select(
                placeholder=tr_lang(self.lang, "Zur Entfernen-Markierung wählen", "Select to mark for removal"),
                min_values=1,
                max_values=len(opts),
                options=opts,
            )
            s.callback = self._mark
            self.add_item(s)
        if officer_mode:
            b_done = discord.ui.Button(label=tr_lang(self.lang, "Grund eingeben …", "Enter reason …"), style=discord.ButtonStyle.danger, row=2)
            b_done.callback = self._finish_officer
            self.add_item(b_done)
        else:
            b_apply = discord.ui.Button(label=tr_lang(self.lang, "Ausgewählte entfernen", "Remove selected"), style=discord.ButtonStyle.danger, row=2)
            b_apply.callback = self._apply_self_btn
            self.add_item(b_apply)
        b_menu = discord.ui.Button(label=tr_lang(self.lang, "◀ Abbrechen / Menü", "◀ Cancel / Menu"), style=discord.ButtonStyle.secondary, row=2)
        b_menu.callback = self._to_menu
        self.add_item(b_menu)
        if self.page > 0:
            b = discord.ui.Button(label=tr_lang(self.lang, "◀ Seite", "◀ Page"), style=discord.ButtonStyle.secondary, row=1)
            b.callback = self._prev
            self.add_item(b)
        if start + LINKED_PAGE_SIZE < len(ordered):
            b2 = discord.ui.Button(label=tr_lang(self.lang, "Seite ▶", "Page ▶"), style=discord.ButtonStyle.secondary, row=1)
            b2.callback = self._next
            self.add_item(b2)

    def _cap_self(self, lang: str = "de-DE") -> str:
        tp = max(1, (len(self.ordered) + LINKED_PAGE_SIZE - 1) // LINKED_PAGE_SIZE)
        acc = len(self.accumulated)
        return tr_lang(
            lang,
            (
                f"**Chars entfernen** — Seite **{self.page + 1}/{tp}** "
                f"({len(self.ordered)} gesamt). Markiert für Entfernen: **{acc}**."
            ),
            (
                f"**Remove characters** — page **{self.page + 1}/{tp}** "
                f"({len(self.ordered)} total). Marked for removal: **{acc}**."
            ),
        )

    def _cap_officer(self, lang: str = "de-DE") -> str:
        assert self.target is not None
        tp = max(1, (len(self.ordered) + LINKED_PAGE_SIZE - 1) // LINKED_PAGE_SIZE)
        acc = len(self.accumulated)
        return tr_lang(
            lang,
            (
                f"**Officer:** Charaktere von {self.target.mention} entfernen.\n"
                f"Seite **{self.page + 1}/{tp}**. Markiert: **{acc}**."
            ),
            (
                f"**Officer:** remove characters from {self.target.mention}.\n"
                f"Page **{self.page + 1}/{tp}**. Marked: **{acc}**."
            ),
        )

    def _caption(self, lang: str = "de-DE") -> str:
        return self._cap_officer(lang) if self.officer_mode else self._cap_self(lang)

    async def _to_menu(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        if self.officer_mode:
            await interaction.response.edit_message(content=tr_lang(glang, "Abgebrochen.", "Cancelled."), view=None)
            return
        await interaction.response.edit_message(
            content=_panel_intro(glang),
            view=_menu_view(self.cog, self.guild, self.actor, actor=self.actor, lang=glang),
        )

    async def _prev(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        nv = LinkedRemovePageView(
            self.cog,
            self.guild,
            self.actor,
            self.ordered,
            self.page - 1,
            officer_mode=self.officer_mode,
            officer=self.officer,
            target=self.target,
            accumulated=self.accumulated,
            lang=glang,
        )
        await interaction.response.edit_message(content=nv._caption(glang), view=nv)

    async def _next(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        nv = LinkedRemovePageView(
            self.cog,
            self.guild,
            self.actor,
            self.ordered,
            self.page + 1,
            officer_mode=self.officer_mode,
            officer=self.officer,
            target=self.target,
            accumulated=self.accumulated,
            lang=glang,
        )
        await interaction.response.edit_message(content=nv._caption(glang), view=nv)

    async def _mark(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        vals = interaction.data.get("values") or []
        for tok in vals:
            pair = self._remove_token_map.get(tok)
            if pair:
                self.accumulated.add((pair[0].strip(), pair[1].strip()))
        nv = self._rebuild_view()
        await interaction.response.edit_message(content=nv._caption(glang), view=nv)

    async def _apply_self_btn(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id or self.officer_mode:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        if not self.accumulated:
            await interaction.response.send_message(tr_lang(glang, "Noch nichts markiert.", "Nothing marked yet."), ephemeral=True)
            return
        await self._apply_self_removal(interaction)

    def _rebuild_view(self) -> "LinkedRemovePageView":
        return LinkedRemovePageView(
            self.cog,
            self.guild,
            self.actor,
            self.ordered,
            self.page,
            officer_mode=self.officer_mode,
            officer=self.officer,
            target=self.target,
            accumulated=self.accumulated,
            lang=self.lang,
        )

    async def _apply_self_removal(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        keys = {(n.lower(), g.lower()) for n, g in self.accumulated}
        linked = await get_linked_list(self.cog.config.member(self.actor))
        new_list = [x for x in linked if (x["name"].lower(), x["game_type"].lower()) not in keys]
        await set_linked_list(self.cog.config.member(self.actor), new_list)
        for n, g in list(self.accumulated):
            m = await get_main_characters(self.cog.config.member(self.actor))
            cur = m.get(g)
            if cur and char_tuple_key(cur["name"], g) == char_tuple_key(n, g):
                await clear_main_for_game(self.cog.config.member(self.actor), g)
        self.accumulated.clear()
        linked2 = await get_linked_list(self.cog.config.member(self.actor))
        if not linked2:
            await interaction.response.edit_message(
                content=tr_lang(
                    glang,
                    "Ausgewählte Chars entfernt. Keine Chars mehr verknüpft.\n\n" + _panel_intro(glang),
                    "Selected characters removed. No characters linked anymore.\n\n" + _panel_intro(glang),
                ),
                view=_menu_view(self.cog, self.guild, self.actor, actor=self.actor, lang=glang),
            )
            return
        ordered = sorted(linked2, key=lambda e: (e["game_type"], e["name"].lower()))
        new_page = min(self.page, max(0, (len(ordered) - 1) // LINKED_PAGE_SIZE))
        await interaction.response.edit_message(
            content=CharMainMenuView._remove_caption(ordered, new_page, glang),
            view=LinkedRemovePageView(
                self.cog, self.guild, self.actor, ordered, new_page, officer_mode=False, lang=glang
            ),
        )

    async def _finish_officer(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.actor.id or not self.officer_mode or self.target is None:
            await interaction.response.send_message(tr_lang(glang, "Ungültig.", "Invalid."), ephemeral=True)
            return
        if not self.accumulated:
            await interaction.response.send_message(
                tr_lang(glang, "Noch nichts markiert — nutze das Dropdown pro Seite.", "Nothing marked yet — use the dropdown per page."),
                ephemeral=True,
            )
            return
        keys = [(n, g) for n, g in self.accumulated]
        await interaction.response.send_modal(
            OfficerRemoveReasonModal(self.cog, self.guild, self.officer or self.actor, self.target, keys, lang=glang)
        )


class OfficerRemoveReasonModal(discord.ui.Modal, title="Begründung"):
    reason = discord.ui.TextInput(
        label="Grund (sichtbar für den User)",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )

    def __init__(
        self,
        cog: "WowGuildAutomation",
        guild: discord.Guild,
        officer: discord.Member,
        target: discord.Member,
        to_remove: List[Tuple[str, str]],
        *,
        lang: str = "en-US",
    ) -> None:
        super().__init__(title=tr_lang(lang, "Begründung", "Reason"))
        self.cog = cog
        self.guild = guild
        self.officer = officer
        self.target = target
        self.to_remove = to_remove
        self.lang = lang
        self.reason.label = tr_lang(lang, "Grund (sichtbar für den User)", "Reason (visible to the user)")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        reason = str(self.reason.value).strip()
        linked = await get_linked_list(self.cog.config.member(self.target))
        rset = {(n.lower(), g.lower()) for n, g in self.to_remove}
        new_list = [x for x in linked if (x["name"].lower(), x["game_type"].lower()) not in rset]
        removed_labels = [f"{n} ({game_label(g)})" for n, g in self.to_remove]
        await set_linked_list(self.cog.config.member(self.target), new_list)
        for n, g in self.to_remove:
            m = await get_main_characters(self.cog.config.member(self.target))
            cur = m.get(g)
            if cur and char_tuple_key(cur["name"], g) == char_tuple_key(n, g):
                await clear_main_for_game(self.cog.config.member(self.target), g)
        cfg = await self.cog.config.guild(self.guild).all()
        templates = cfg.get("templates", {})
        dm_t = templates.get(
            "admin_removed_char_dm",
            "Ein Offizier hat folgende WoW-Chars von dir entfernt: {chars}\nGrund: {reason}",
        )
        try:
            await self.target.send(
                dm_t.format(
                    chars=", ".join(removed_labels),
                    reason=reason,
                    officer=self.officer.display_name,
                )
            )
        except discord.HTTPException:
            pass
        await interaction.response.send_message(
            tr_lang(
                glang,
                f"Entfernt bei {self.target.mention}: {', '.join(removed_labels)}",
                f"Removed from {self.target.mention}: {', '.join(removed_labels)}",
            ),
            ephemeral=True,
        )


def officer_can_manage_characters(member: discord.Member) -> bool:
    return member.guild_permissions.manage_guild or member.guild_permissions.administrator


class OfficerListMenuView(discord.ui.View):
    """Officer: list character links — all members or pick users (multi)."""

    def __init__(self, cog: "WowGuildAutomation", guild: discord.Guild, officer: discord.Member, *, lang: str = "en-US") -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.guild = guild
        self.officer = officer
        self.lang = lang
        self.all_btn.label = tr_lang(lang, "Alle mit verknüpften Chars", "All with linked characters")
        self.pick_btn.label = tr_lang(lang, "Bestimmte Mitglieder wählen", "Pick specific members")

    @discord.ui.button(label="Alle mit verknüpften Chars", style=discord.ButtonStyle.primary, row=0)
    async def all_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.officer.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = await self.cog._officer_format_all_linked_chars(self.guild)
        empty = tr_lang(glang, "Keine Einträge.", "No entries.")
        for chunk in [text[i : i + 1900] for i in range(0, len(text), 1900)] or [empty]:
            await interaction.followup.send(chunk, ephemeral=True)

    @discord.ui.button(label="Bestimmte Mitglieder wählen", style=discord.ButtonStyle.secondary, row=0)
    async def pick_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.officer.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        await interaction.response.edit_message(
            content=tr_lang(glang, "Wähle bis zu 25 Mitglieder:", "Pick up to 25 members:"),
            view=OfficerUserPickView(self.cog, self.guild, self.officer, lang=glang),
        )


class OfficerUserPickView(discord.ui.View):
    def __init__(
        self,
        cog: "WowGuildAutomation",
        guild: discord.Guild,
        officer: discord.Member,
        *,
        back_view_factory: Optional[Callable[[], discord.ui.View]] = None,
        back_content: Optional[str] = None,
        lang: str = "en-US",
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.officer = officer
        self.lang = lang
        self._back_view_factory = back_view_factory
        self._back_content = back_content
        self.user_select = discord.ui.UserSelect(
            placeholder=tr_lang(self.lang, "Mitglieder (mehrfach)", "Members (multiple)"),
            min_values=1,
            max_values=25,
            custom_id="officer_user_pick",
        )
        self.user_select.callback = self._on_users
        self.add_item(self.user_select)
        b = discord.ui.Button(label=tr_lang(self.lang, "◀ Zurück", "◀ Back"), style=discord.ButtonStyle.secondary, row=1)
        b.callback = self._back
        self.add_item(b)

    async def _back(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.officer.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        if self._back_view_factory is not None:
            await interaction.response.edit_message(
                content=self._back_content or _admin_panel_intro(glang),
                view=self._back_view_factory(),
            )
            return
        await interaction.response.edit_message(
            content=tr_lang(glang, "Wähle eine Option:", "Pick an option:"),
            view=OfficerListMenuView(self.cog, self.guild, self.officer, lang=glang),
        )

    async def _on_users(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.officer.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        users = self.user_select.values
        await interaction.response.defer(ephemeral=True)
        lines: List[str] = []
        for u in users:
            m = self.guild.get_member(u.id)
            if not m:
                continue
            lines.append(await self.cog._format_user_char_list_ephemeral(self.guild, m, header_user=True))
        msg = "\n\n".join(lines) if lines else tr_lang(glang, "Keine gültigen Mitglieder.", "No valid members.")
        for chunk in [msg[i : i + 1900] for i in range(0, len(msg), 1900)] or ["—"]:
            await interaction.followup.send(chunk, ephemeral=True)
        self.stop()


class WowAdminCharPanelView(discord.ui.View):
    """One panel: pick a member + lists — replaces several /wow-admin sub-actions."""

    def __init__(self, cog: "WowGuildAutomation", guild: discord.Guild, officer: discord.Member, *, lang: str = "en-US") -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.guild = guild
        self.officer = officer
        self.lang = lang
        self.pick_member.placeholder = tr_lang(lang, "Mitglied zum Bearbeiten wählen", "Pick a member to edit")
        self.all_linked.label = tr_lang(lang, "Alle mit verknüpften Chars", "All with linked characters")
        self.pick_list.label = tr_lang(lang, "Bestimmte Mitglieder listen", "List specific members")

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Mitglied zum Bearbeiten wählen",
        row=0,
        min_values=1,
        max_values=1,
    )
    async def pick_member(self, interaction: discord.Interaction, select: discord.ui.UserSelect) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.officer.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        u = select.values[0]
        member = self.guild.get_member(u.id)
        if member is None:
            await interaction.response.send_message(tr_lang(glang, "Mitglied ist nicht (mehr) auf dem Server.", "Member is no longer on the server."), ephemeral=True)
            return
        header = tr_lang(
            glang,
            f"**Bearbeitest:** {member.mention} (`{member.display_name}`)\n\n{_panel_intro(glang)}",
            f"**Editing:** {member.mention} (`{member.display_name}`)\n\n{_panel_intro(glang)}",
        )
        await interaction.response.edit_message(
            content=header,
            view=CharMainMenuView(self.cog, self.guild, member, actor=self.officer, lang=glang),
        )

    @discord.ui.button(label="Alle mit verknüpften Chars", style=discord.ButtonStyle.primary, row=1)
    async def all_linked(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.officer.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = await self.cog._officer_format_all_linked_chars(self.guild)
        empty = tr_lang(glang, "Keine Einträge.", "No entries.")
        for chunk in [text[i : i + 1900] for i in range(0, len(text), 1900)] or [empty]:
            await interaction.followup.send(chunk, ephemeral=True)

    @discord.ui.button(label="Bestimmte Mitglieder listen", style=discord.ButtonStyle.secondary, row=1)
    async def pick_list(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.officer.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return

        def _back() -> discord.ui.View:
            return WowAdminCharPanelView(self.cog, self.guild, self.officer, lang=glang)

        await interaction.response.edit_message(
            content=tr_lang(glang, "Wähle bis zu 25 Mitglieder:", "Pick up to 25 members:"),
            view=OfficerUserPickView(
                self.cog,
                self.guild,
                self.officer,
                back_view_factory=_back,
                back_content=_admin_panel_intro(glang),
                lang=glang,
            ),
        )


class SlashWowAdminSyncAllConfirmView(discord.ui.View):
    """Dropdown confirmation before bulk rank sync."""

    def __init__(self, cog: "WowGuildAutomation", guild: discord.Guild, officer: discord.Member, *, lang: str = "en-US") -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.guild = guild
        self.officer = officer
        self.lang = lang
        sel = discord.ui.Select(
            placeholder=tr_lang(self.lang, "Aktion bestätigen", "Confirm action"),
            options=[
                discord.SelectOption(
                    label=tr_lang(self.lang, "Jetzt alle (mit Main) synchronisieren — kann dauern", "Sync all (with a main) now — may take a while"),
                    value="confirm",
                ),
            ],
        )
        sel.callback = self._confirm
        self.add_item(sel)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        glang = await self.cog._guild_lang(self.guild)
        if interaction.user.id != self.officer.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = await self.cog._slash_admin_sync_all_members_report(self.guild)
        for chunk in [text[i : i + 1900] for i in range(0, len(text), 1900)] or ["—"]:
            await interaction.followup.send(chunk, ephemeral=True)
        self.stop()


def attach_officer_extras_if_needed(view: CharMainMenuView) -> None:
    """Extra buttons when an officer edits another member."""
    if view.actor.id == view.member.id:
        return

    sync_b = discord.ui.Button(label=tr_lang(view.lang, "Rang-Sync", "Rank sync"), style=discord.ButtonStyle.success, row=2)

    async def _sync_cb(interaction: discord.Interaction) -> None:
        glang = await view.cog._guild_lang(view.guild)
        if interaction.user.id != view.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = await view.cog._slash_admin_sync_report_for_member(view.guild, view.member)
        await interaction.followup.send(text[:1900], ephemeral=True)

    sync_b.callback = _sync_cb
    view.add_item(sync_b)

    switch_b = discord.ui.Button(label=tr_lang(view.lang, "Anderes Mitglied", "Another member"), style=discord.ButtonStyle.secondary, row=2)

    async def _switch_cb(interaction: discord.Interaction) -> None:
        glang = await view.cog._guild_lang(view.guild)
        if interaction.user.id != view.actor.id:
            await interaction.response.send_message(tr_lang(glang, "Nur für dich.", "Only for you."), ephemeral=True)
            return
        await interaction.response.edit_message(
            content=_admin_panel_intro(glang),
            view=WowAdminCharPanelView(view.cog, view.guild, view.actor, lang=glang),
        )

    switch_b.callback = _switch_cb
    view.add_item(switch_b)

# WoWToolsForever/gearcheck.py
from typing import List, Literal, Optional, Dict

import discord
from discord import app_commands
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n, set_contextual_locales_from_guild
from .autocomplete import _LANG_CODES
from .groups import wowtf
from .forever_api import (
    display_name,
    explain_character_error,
    fetch_character,
    fetch_item_levels,
    resolve_character_args,
    wowhead_url,
)
from .pdc_dashboard import tr_lang

_ = Translator("WoWToolsForever", __file__)

def _resolve_locale(lang_or_locale: str) -> str:
    if not lang_or_locale:
        return "en_US"
    key = lang_or_locale.lower()
    return _LANG_CODES.get(key, lang_or_locale)  # "de" -> "de_DE", passes full locales through

def _wowhead_url(item_id: int, game: str = "forever") -> str:
    # WoW Forever has its own Wowhead path (/forever/)
    return wowhead_url("item", item_id)

def _quality_emoji(quality_type: str) -> str:
    q = (quality_type or "").upper()
    return {
        "LEGENDARY": "🟧",
        "EPIC": "🟪",
        "RARE": "🟦",
        "UNCOMMON": "🟩",
        "COMMON": "⬜",
    }.get(q, "🔳")

def _is_socket_enchant(ench: dict) -> bool:
    """
    Heuristic: gem sockets usually have source_item (the gem) and slot IDs 1..4.
    BONUS_SOCKETS (id: 6) is just an extra socket, not a gem itself.
    """
    if not ench:
        return False
    if ench.get("source_item"):
        slot = ench.get("enchantment_slot", {}) or {}
        slot_id = slot.get("id")
        return slot_id in {1, 2, 3, 4}
    return False

async def _fetch_equipment_blizzard(self, *, region: str, name: str, surname: str,
                                    locale: str = "en_US") -> dict:
    return await fetch_character(self, region, name, surname, "equipment", locale=locale)


@cog_i18n(_)
class GearCheck(commands.Cog):
    """Gear check via the Blizzard Profile API (WoW Forever), incl. detailed iLvl fetch and 'Socket' label."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot

    # --------------- Blizzard API: item level per item ID ---------------
    async def _fetch_item_levels(
        self,
        *,
        region: str,
        game: str,
        locale: str,
        item_ids: List[int],
        concurrency: int = 5,
    ) -> Dict[int, Optional[int]]:
        """
        Fetches the item level per item ID from /data/wow/item/{id} (static namespace).
        Returns Dict {item_id: level or None}.
        """
        return await fetch_item_levels(self, region, item_ids, locale=locale, concurrency=concurrency)

    # --------------- Command ---------------
    @wowtf.command(
        name="gearcheck",
        description="Show a character's equipped gear with item level, sockets and enchants.",
        extras={"i18n_desc": {
            "de-DE": "Zeigt die angelegte Ausrüstung eines Charakters mit Itemlevel, Sockeln und Verzauberungen.",
            "en-US": "Show a character's equipped gear with item level, sockets and enchants.",
        }},
    )
    @app_commands.describe(
        region="Region (eu/us/kr/tw); empty = your saved character region",
        name="Character first name; empty = your saved character",
        surname="Character surname; empty = your saved character",
        locale="Locale (e.g. de or de_DE, en or en_US)",
    )
    async def gearcheck(self, ctx, region: Optional[Literal["eu", "us", "kr", "tw"]] = None,
                        name: Optional[str] = None, surname: Optional[str] = None,
                        locale: str = "en", private: bool = True):
        """Show a character's currently equipped gear (incl. iLvl fetch & socket/enchant labels)."""
        game = "forever"
        if ctx.interaction:
            await set_contextual_locales_from_guild(self.bot, ctx.guild)

        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"
        region, name, surname, err = await resolve_character_args(self, ctx, region, name, surname)
        if err:
            return await ctx.send(err, ephemeral=True)

        locale = _resolve_locale(locale)

        try:
            await ctx.defer(ephemeral=private)
        except Exception:
            pass

        # 1) Load equipment
        try:
            data = await _fetch_equipment_blizzard(
                self, region=region, name=name, surname=surname, locale=locale
            )
        except Exception as e:
            msg = await explain_character_error(
                self, lang, e, region=region, name=name, surname=surname,
                feature_de="Ausrüstung", feature_en="Equipment",
                prefix_de="Fehler beim Abrufen der Ausrüstung", prefix_en="Failed to fetch gear",
            )
            return await ctx.send(msg, ephemeral=bool(ctx.interaction))

        equipped = data.get("equipped_items") or []
        if not equipped:
            return await ctx.send(
                tr_lang(lang, "Keine Ausrüstung gefunden.", "No gear found."),
                ephemeral=bool(ctx.interaction),
            )

        # 2) Reload item level per item
        item_ids = [it.get("item", {}).get("id") for it in equipped if it.get("item")]
        ilvls_by_id = await self._fetch_item_levels(
            region=region, game=game, locale=locale, item_ids=item_ids
        )

        # 3) Build output (mind the 2000-char limit)
        lines: List[str] = []
        hidden_count = 0

        for idx, it in enumerate(equipped):
            try:
                slot_name = it["slot"]["name"]
                quality_type = it.get("quality", {}).get("type", "COMMON")
                emoji = _quality_emoji(quality_type)
                item_name = it.get("name", "Unknown")
                item_id = it.get("item", {}).get("id")
                ilvl = ilvls_by_id.get(item_id)
                ilvl_str = f"ilvl {ilvl}" if ilvl is not None else "ilvl ?"

                link = _wowhead_url(item_id, game) if item_id else None
                head = (
                    f"**{slot_name}**: {emoji} [{item_name}]({link}) ({ilvl_str})"
                    if link
                    else f"**{slot_name}**: {emoji} {item_name} ({ilvl_str})"
                )
                lines.append(head)

                # Enchants / Sockets
                for ench in it.get("enchantments", []) or []:
                    d = ench.get("display_string")
                    if not d:
                        continue
                    if _is_socket_enchant(ench):
                        lines.append(f"`└──` **Socket:** {d}")
                    else:
                        lines.append(f"`└──` **Enchant:** {d}")

            except Exception:
                # Defensive: a single broken item shouldn't kill everything
                continue

            # Soft limit so we stay under 2000 characters. Count remaining *items*
            # (lines also contain enchant/socket rows, so len(lines) is not usable).
            if sum(len(x) + 1 for x in lines) > 1800:
                hidden_count = max(0, len(equipped) - (idx + 1))
                break

        if hidden_count > 0:
            lines.append(tr_lang(lang, f"... und {hidden_count} weitere Einträge.", f"... and {hidden_count} more entries."))

        embed = discord.Embed(
            title=f"{display_name(name, surname)} ({region.upper()}) [{game.capitalize()}]",
            description="\n".join(lines),
            color=await ctx.embed_color(),
        )

        ephemeral = private if ctx.interaction else False
        await ctx.send(embed=embed, ephemeral=ephemeral)

    # --------- Autocomplete ---------
    @gearcheck.autocomplete("locale")
    async def ac_locale(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        cur = (current or "").lower()

        # Build suggestions: ("Deutsch (de_DE)", "de_DE") + ("Deutsch (de)", "de")
        display_map = {
            "de": "Deutsch", "en": "English", "fr": "Français", "es": "Español",
            "it": "Italiano", "pt": "Português", "ru": "Русский",
        }

        pairs: List[tuple[str, str]] = []
        for short, full in _LANG_CODES.items():
            label_base = display_map.get(short, short)
            # full locale
            pairs.append((f"{label_base} ({full})", full))
            # short code
            pairs.append((f"{label_base} ({short})", short))

        # filter
        out = [
            app_commands.Choice(name=label, value=val)
            for (label, val) in pairs
            if cur in label.lower() or cur in val.lower()
        ][:25]
        return out



async def setup(bot: Red):
    await bot.add_cog(GearCheck(bot))

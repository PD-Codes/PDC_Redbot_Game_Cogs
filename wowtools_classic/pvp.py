import asyncio
import re
import time
from typing import List, Optional

import discord
from discord import app_commands
from redbot.core import commands
from redbot.core.i18n import Translator, set_contextual_locales_from_guild

from .groups import WoWToolsGroups
from .blizzard_rest import BlizzardAPIError, api_get, fetch_character, realm_slug
from .exceptions import InvalidBlizzardAPI
from .pdc_dashboard import tr_lang
from .utils import get_realms

_ = Translator("WoWTools", __file__)

# Season reward titles, matched on the en_US achievement names, e.g.
# "Gladiator: The War Within Season 2" or "Combatant I: Midnight Season 1".
# Higher number = better reward. Replaces the old hard-coded achievement ids.
_PVP_TITLE_ORDER = (
    ("combatant i", 1),
    ("combatant ii", 2),
    ("challenger i", 3),
    ("challenger ii", 4),
    ("rival i", 5),
    ("rival ii", 6),
    ("duelist", 7),
    ("elite", 8),
    ("gladiator", 9),
    ("legend", 10),
    ("strategist", 10),
    ("hero of the horde", 11),
    ("hero of the alliance", 11),
)
_RE_SEASON_ACHI = re.compile(r"^(?P<title>[^:]+):\s*(?P<season>.*\bSeason\s+\d+)\s*$", re.IGNORECASE)
_PVP_SEASON_TTL = 6 * 3600  # seconds


def _title_rank(title: str) -> int:
    t = title.strip().casefold()
    best = 0
    for key, rank in _PVP_TITLE_ORDER:
        # exact title or a named variant ("Malevolent Gladiator") - but not "Rival II" for "Rival I"
        if t == key or t.endswith(" " + key):
            best = max(best, rank)
    return best


def _season_achievements(achievements: dict, limit: int = 4) -> List[str]:
    """Best completed PvP season title per season, newest seasons first."""
    per_season: dict[str, tuple[int, int, str]] = {}
    for entry in (achievements or {}).get("achievements") or []:
        if not isinstance(entry, dict) or not entry.get("completed_timestamp"):
            continue
        achi = entry.get("achievement") or {}
        name = achi.get("name")
        if not isinstance(name, str):
            continue
        m = _RE_SEASON_ACHI.match(name)
        if not m:
            continue
        rank = _title_rank(m.group("title"))
        if not rank:
            continue
        season = m.group("season").strip()
        achi_id = int(entry.get("id") or achi.get("id") or 0)
        prev = per_season.get(season)
        if prev is None or rank > prev[0]:
            per_season[season] = (rank, max(achi_id, prev[1] if prev else 0), name)
        else:
            per_season[season] = (prev[0], max(achi_id, prev[1]), prev[2])
    # Newer seasons have higher achievement ids.
    ordered = sorted(per_season.values(), key=lambda v: v[1], reverse=True)
    return [v[2] for v in ordered[:limit]]


class PvP:
    @commands.cooldown(rate=1, per=5, type=commands.BucketType.user)
    @WoWToolsGroups.main_group.command(name="rating")
    @commands.bot_has_permissions(embed_links=True)
    async def rating(self, ctx, character: str, *, realm: str):
        """Check a character's PVP ratings."""
        if ctx.interaction:
            # There is no contextual locale for interactions, so we need to set it manually
            # (This is probably a bug in Red, remove this when it's fixed)
            await set_contextual_locales_from_guild(self.bot, ctx.guild)
        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"

        if ":" in realm:
            realm, region = realm.rsplit(sep=":", maxsplit=1)
            region = region.strip().lower()
        else:
            region = await self.config.guild(ctx.guild).region() if ctx.guild else None
        if not region:
            await ctx.send(
                tr_lang(
                    lang,
                    "Keine Region angegeben. Nutze `Realm:Region` (z. B. `Ragnaros:EU`).",
                    "No region given. Use `Realm:Region` (e.g. `Ragnaros:EU`).",
                )
            )
            return

        realm = realm_slug(realm, region)

        rbg_rating = "0"
        duo_rating = "0"
        tri_rating = "0"

        await ctx.defer()
        try:
            await self.limiter.acquire()
            profile = await fetch_character(self, region, realm, character)
        except InvalidBlizzardAPI:
            await ctx.send(
                _(
                    "The Blizzard API is not properly set up.\n"
                    "Create a client on https://develop.battle.net/ and then type in "
                    "`{prefix}set api blizzard client_id,whoops client_secret,whoops` "
                    "filling in `whoops` with your client's ID and secret."
                ).format(prefix=ctx.prefix)
            )
            return
        except BlizzardAPIError as e:
            if e.status == 403:
                await ctx.send(
                    tr_lang(
                        lang,
                        "PvP-Wertungen sind für dieses Spiel über die Blizzard-API nicht verfügbar.",
                        "PvP ratings are not available for this game via the Blizzard API.",
                    )
                )
                return
            await ctx.send(
                _('Character "{character_name}" not found.').format(character_name=character)
            )
            return

        async def optional(sub: str) -> dict:
            # Sub resources may be missing (no rated games, endpoint not in namespace).
            try:
                await self.limiter.acquire()
                return await fetch_character(self, region, realm, character, sub)
            except (BlizzardAPIError, InvalidBlizzardAPI, asyncio.TimeoutError):
                return {}

        achievements = await optional("achievements")
        media = await optional("character-media")
        rbg_statistics = await optional("pvp-bracket/rbg")
        duo_statistics = await optional("pvp-bracket/2v2")
        tri_statistics = await optional("pvp-bracket/3v3")
        shuffle_rating, shuffle_rank = await self.get_shuffle_entry(region, profile)

        if "name" not in profile:
            await ctx.send(_("That character or realm does not exist."))
            return

        real_char_name: str = profile["name"]
        char_img_url = self._media_url(media)
        char_race: str = (profile.get("race") or {}).get("name", "?")
        char_class: str = (profile.get("character_class") or {}).get("name", "?")
        char_faction: str = (profile.get("faction") or {}).get("name", "")

        achi_to_post = _season_achievements(achievements)
        if char_faction != "Horde":
            color = discord.Color.blue()
        else:
            color = discord.Color.red()

        if "rating" in rbg_statistics:
            rbg_rating: str = rbg_statistics["rating"]
        if "rating" in duo_statistics:
            duo_rating: str = duo_statistics["rating"]
        if "rating" in tri_statistics:
            tri_rating: str = tri_statistics["rating"]

        embed = discord.Embed(
            color=color,
            title=real_char_name,
            description=f"{char_race} {char_class}",
            url=f"https://worldofwarcraft.com/en-gb/character/{region}/{realm}/{real_char_name}",
        )
        if char_img_url:
            embed.set_thumbnail(url=char_img_url)
        embed.add_field(name=_("RBG Rating"), value=rbg_rating)
        embed.add_field(name=_("2v2 Rating"), value=duo_rating)
        embed.add_field(name=_("3v3 Rating"), value=tri_rating)
        embed.add_field(
            name=_("Shuffle Rating"),
            value=shuffle_rating or _("Character not on the leaderboard"),
        )
        if shuffle_rank:
            embed.add_field(name=_("Shuffle Rank"), value=shuffle_rank)
        if achi_to_post:
            embed.add_field(name=_("Achievements"), value="\n".join(achi_to_post), inline=False)

        details_url = (
            f"https://check-pvp.fr/"
            f"{region}/"
            f"{realm.capitalize()}/"
            f"{character.capitalize()}"
        )
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label=_("More details"), style=discord.ButtonStyle.link, url=details_url
            )
        )

        await ctx.send(embed=embed, view=view)

    @rating.autocomplete("realm")
    async def rating_realm_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        region = None
        if interaction.guild:
            region = await self.config.guild(interaction.guild).region()
        realms = await get_realms(self, current, region)
        return realms[:25]

    @staticmethod
    def _media_url(media: dict) -> Optional[str]:
        assets = [a for a in (media or {}).get("assets") or [] if isinstance(a, dict)]
        for wanted in ("avatar", "inset", "main"):
            for a in assets:
                if a.get("key") == wanted and a.get("value"):
                    return a["value"]
        if assets and assets[0].get("value"):
            return assets[0]["value"]
        # Older profile media format
        return (media or {}).get("avatar_url")

    async def get_current_pvp_season_id(self, region: str) -> Optional[int]:
        """Current PvP season id from ``/data/wow/pvp-season/index`` (cached 6h)."""
        cache = getattr(self, "_pvp_season_cache", None)
        if cache is None:
            cache = self._pvp_season_cache = {}
        hit = cache.get(region)
        if hit and time.time() - hit[0] < _PVP_SEASON_TTL:
            return hit[1]
        try:
            js = await api_get(self, region, "/data/wow/pvp-season/index", kind="dynamic")
        except (BlizzardAPIError, InvalidBlizzardAPI, asyncio.TimeoutError):
            return None
        season_id = (js.get("current_season") or {}).get("id")
        if season_id is None:
            ids = [s.get("id") for s in js.get("seasons") or [] if isinstance(s, dict)]
            season_id = max((i for i in ids if isinstance(i, int)), default=None)
        if season_id is not None:
            cache[region] = (time.time(), season_id)
        return season_id

    async def get_shuffle_entry(self, region: str, profile: dict) -> tuple[Optional[int], Optional[str]]:
        """(rating, rank string) of the character on the Solo Shuffle leaderboard."""
        leaderboard = await self.get_shuffle_leaderboard(region, profile)
        entry = next(
            (
                e
                for e in (leaderboard or {}).get("entries") or []
                if isinstance(e, dict) and (e.get("character") or {}).get("name") == profile.get("name")
            ),
            None,
        )
        if not entry:
            return None, None
        rank: Optional[int] = entry.get("rank")
        if not rank:
            return entry.get("rating"), None

        if 11 <= (rank % 100) <= 13:
            suffix = "th"
        else:
            suffix = ["th", "st", "nd", "rd", "th"][min(rank % 10, 4)]
        return entry.get("rating"), str(rank) + suffix

    async def get_shuffle_leaderboard(self, region: str, profile: dict) -> dict:
        char_class: str = (profile.get("character_class") or {}).get("name") or ""
        char_class = char_class.lower().replace(" ", "")

        char_spec: str = (profile.get("active_spec") or {}).get("name") or ""
        char_spec = char_spec.lower().replace(" ", "")
        if not char_class or not char_spec:
            return {}

        season_id = await self.get_current_pvp_season_id(region)
        if season_id is None:
            return {}
        pvp_bracket = f"shuffle-{char_class}-{char_spec}"
        try:
            await self.limiter.acquire()
            # Solo Shuffle does not exist in classic -> 404, handled as "not on leaderboard".
            return await api_get(
                self,
                region,
                f"/data/wow/pvp-season/{season_id}/pvp-leaderboard/{pvp_bracket}",
                kind="dynamic",
            )
        except (BlizzardAPIError, InvalidBlizzardAPI, asyncio.TimeoutError):
            return {}

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import aiohttp
import discord
from dateutil.parser import isoparse
from discord import app_commands
from redbot.core import commands
from redbot.core.i18n import Translator, set_contextual_locales_from_guild
from redbot.core.utils.views import _ACCEPTABLE_PAGE_TYPES, SimpleMenu

from .autocomplete import GAME
from .pdc_dashboard import tr_lang

log = logging.getLogger("red.karlo-cogs.wowtools")
_ = Translator("WoWTools", __file__)

# Public raider.io API of MoP Classic (documented at https://classic.raider.io/api).
# It offers character and guild profiles only - no Mythic+ (no M+ in MoP) and no
# static-data endpoints. raiderio-async is hard-wired to https://raider.io (retail).
CLASSIC_RIO_BASE = "https://classic.raider.io"

# raider.io expansion ids (10 = The War Within, 11 = Midnight). The highest id that
# still returns static data is probed upwards from here and cached.
_RIO_MIN_EXPANSION_ID = 10
_RIO_STATIC_TTL = 6 * 3600  # seconds


def rio_not_available_msg(lang: str) -> str:
    return tr_lang(
        lang,
        "Diese raider.io-Funktion ist für MoP Classic nicht verfügbar.",
        "This raider.io feature is not available for MoP Classic.",
    )


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = isoparse(str(value))
    except (ValueError, OverflowError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Raiderio:
    """Cog for interaction with the classic.raider.io API"""

    @commands.cooldown(rate=1, per=5, type=commands.BucketType.user)
    @commands.group(name="wowtc-raiderio", aliases=["wowtc-rio"])
    async def raiderio(self, ctx: commands.Context):
        """Commands for interacting with classic.raider.io"""
        pass

    @raiderio.command(name="profile")
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    @app_commands.describe(
        character="Name of the character to look up",
        realm="The character's realm",
    )
    async def raiderio_profile(self, ctx, character: str, *, realm: str) -> None:
        """Display the classic.raider.io profile of a character.

        **Example:**
        [p]raiderio profile Karlo Ragnaros
        """
        if ctx.interaction:
            # There is no contextual locale for interactions, so we need to set it manually
            # (This is probably a bug in Red, remove this when it's fixed)
            await set_contextual_locales_from_guild(self.bot, ctx.guild)

        try:
            realm, region = realm.split(sep=":", maxsplit=1)
        except ValueError:
            region = await self.config.guild(ctx.guild).region()
            if not region:
                await ctx.send(
                    _(
                        "No region provided, and no default region set.\nSet a default region with `{prefix}wowset region` or provide a region with the realm like `{prefix}raiderio profile Karlo Ragnaros:EU`"
                    ).format(prefix=ctx.prefix)
                )
                return
        realm = ("-".join(realm).lower() if isinstance(realm, tuple) else realm.lower()).replace(
            " ", "-"
        )

        region = region.lower()
        if ctx.interaction:
            await ctx.defer()
        profile_data = await self.rio_character_profile(
            region,
            realm,
            character,
            fields=[
                "mythic_plus_scores_by_season:current",
                "raid_progression",
                "gear",
                "mythic_plus_best_runs",
                "talents",
                "guild",
            ],
        )

        try:
            char_name = profile_data["name"]
        except KeyError:
            await ctx.send(_("Character not found."))
            return
        # All of the following fields can be missing or null in the API
        # response, so fall back to "N/A" instead of crashing.
        char_race = profile_data.get("race", "N/A")
        char_spec = profile_data.get("active_spec_name", "N/A")
        char_class = profile_data.get("class", "N/A")
        char_guild = (profile_data.get("guild") or {}).get("name")
        char_image = profile_data.get("thumbnail_url")
        seasons = profile_data.get("mythic_plus_scores_by_season") or []
        char_score = ((seasons[0].get("segments") or {}).get("all") if seasons else None) or {}
        char_score_color = int("0x" + str(char_score.get("color", "#ffffff"))[1:], 0)
        char_raid = self.raid_summary(
            profile_data.get("raid_progression"), await self.get_current_raid(region)
        )
        last_crawled_at = profile_data.get("last_crawled_at")
        char_last_updated = self.parse_date(last_crawled_at) if last_crawled_at else "N/A"
        char_gear = profile_data.get("gear") or {}
        char_ilvl = char_gear.get("item_level_equipped", "N/A")
        char_url = profile_data.get("profile_url")
        char_talents = (profile_data.get("talentLoadout") or {}).get("loadout_text") or ""

        banner = profile_data.get("profile_banner")

        banner_url = (
            f"https://cdnassets.raider.io/images/profile/masthead_backdrops/v2/{banner}.jpg"
            if banner
            else None
        )
        armory_url = f"https://worldofwarcraft.com/en-gb/character/{region}/{realm}/{char_name}"
        wcl_url = f"https://classic.warcraftlogs.com/character/{region}/{realm}/{char_name}"
        raidbots_url = (
            f"https://www.raidbots.com/simbot/quick?region={region}&realm={realm}&name={char_name}"
        )

        # First page
        description = f"{char_race} {char_spec} {char_class}"
        if char_guild:
            description += f"\n<{char_guild}>"
        embed = discord.Embed(
            title=char_name,
            url=char_url,
            description=description,
            color=char_score_color,
        )
        embed.set_author(
            name=_("classic.raider.io profile"),
            icon_url="https://cdnassets.raider.io/images/fb_app_image.jpg",
        )
        embed.set_thumbnail(url=char_image)
        embed.add_field(
            name=_("__**Mythic+ Score**__"),
            value=char_score.get("score", "N/A"),
            inline=False,
        )
        embed.add_field(name=_("Raid progress"), value=char_raid, inline=True)
        embed.add_field(name=_("Item level"), value=char_ilvl, inline=True)
        embed.add_field(
            name=_("__Other links__"),
            value=_(
                "[Armory]({armory_url}) | [WarcraftLogs]({wcl_url}) | [Raidbots]({raidbots_url})"
            ).format(
                armory_url=armory_url,
                wcl_url=wcl_url,
                raidbots_url=raidbots_url,
            ),
            inline=False,
        )
        if banner_url:
            embed.set_image(url=banner_url)
        embed.set_footer(
            text=_("Last updated: {char_last_updated}").format(char_last_updated=char_last_updated)
        )
        embeds = [embed]

        # As of TWW, fortified and tyrannical no longer track separately
        #
        # if runs := self.get_all_runs(profile_data):
        #     data = [
        #         [dungeon, runs["Fortified"], runs["Tyrannical"]] for dungeon, runs in runs.items()
        #     ]
        #     tabulated = tabulate(
        #         data,
        #         headers=[_("Dungeon"), _("Forti"), _("Tyrann")],
        #         tablefmt="simple",
        #         colalign=("left", "right", "right"),
        #     )

        #     embed = discord.Embed(
        #         title=char_name,
        #         description=box(
        #             tabulated,
        #         ),
        #         url=char_url,
        #         color=char_score_color,
        #     )
        #     embed.set_author(
        #         name=_("classic.raider.io profile"),
        #         icon_url="https://cdnassets.raider.io/images/fb_app_image.jpg",
        #     )
        #     embed.set_thumbnail(url=char_image)
        #     embed.set_footer(
        #         text=_("Last updated: {char_last_updated}").format(
        #             char_last_updated=char_last_updated
        #         )
        #     )
        #     embeds.append(embed)

        # Gear page
        embed = await self.make_gear_embed(
            char_gear,
            char_image,
            char_last_updated,
            char_name,
            char_score_color,
            char_url,
        )
        embeds.append(embed)

        await ProfileMenu(pages=embeds, talents=char_talents, disable_after_timeout=True).start(
            ctx
        )

    # As of TWW, fortified and tyrannical no longer track separately
    #
    # @staticmethod
    # def get_all_runs(profile_data: dict) -> dict[str, dict[str, list[str]]]:
    #     """Extracts info about a player's Mythic+ dungeon runs from their classic.raider.io profile data.

    #     Args:
    #         profile_data (dict): A dictionary containing the player's classic.raider.io profile data.

    #     Returns:
    #         dict: A dictionary containing information about the player's runs in each dungeon.
    #         The keys of the dictionary are the names of the dungeons, and the values are
    #         dictionaries with keys "Fortified" and "Tyrannical" and values that are lists of
    #         strings representing the key levels completed with the corresponding affixes.
    #     """
    #     runs = {}
    #     existing_dungeons = set()

    #     best_runs = profile_data["mythic_plus_best_runs"]
    #     alt_runs = profile_data["mythic_plus_alternate_runs"]

    #     for run in best_runs:
    #         dungeon_name: str = run["short_name"]
    #         normalized_name = dungeon_name.upper()
    #         key_level = run["mythic_level"]
    #         if normalized_name not in existing_dungeons:
    #             runs[dungeon_name] = {"Fortified": "", "Tyrannical": ""}
    #             existing_dungeons.add(normalized_name)
    #         if run["affixes"][0]["id"] == 10:  # Fortified
    #             runs[dungeon_name]["Fortified"] = f"+{key_level}"
    #         elif run["affixes"][0]["id"] == 9:  # Tyrannical
    #             runs[dungeon_name]["Tyrannical"] = f"+{key_level}"

    #     for alt_run in alt_runs:
    #         dungeon_name: str = alt_run["short_name"]
    #         normalized_name = dungeon_name.upper()
    #         if normalized_name in existing_dungeons:
    #             key_level = alt_run["mythic_level"]
    #             if alt_run["affixes"][0]["id"] == 10:  # Fortified
    #                 runs[dungeon_name]["Fortified"] = f"+{key_level}"
    #             elif alt_run["affixes"][0]["id"] == 9:  # Tyrannical
    #                 runs[dungeon_name]["Tyrannical"] = f"+{key_level}"
    #     return runs

    @raiderio.command(name="guild")
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    @app_commands.describe(guild="The name of the guild", realm="The guild's realm")
    async def raiderio_guild(self, ctx: commands.Context, guild: str, *, realm: str) -> None:
        """Display the classic.raider.io profile of a guild.

        If the guild or realm name have spaces in them, they need to be enclosed in quotes.

        Example:
        [p]raiderio guild "Jahaci Rumene Kadulje" Ragnaros
        """
        if ctx.interaction:
            # There is no contextual locale for interactions, so we need to set it manually
            # (This is probably a bug in Red, remove this when it's fixed)
            await set_contextual_locales_from_guild(self.bot, ctx.guild)

        try:
            realm, region = realm.split(sep=":")
        except ValueError:
            region = await self.config.guild(ctx.guild).region()
            if not region:
                await ctx.send(
                    _(
                        'No region provided, and no default region set.\nSet a default region with `{prefix}wowset region` or provide a region with the realm like `{prefix}raiderio guild "Jahaci Rumene Kadulje" Ragnaros:EU`'
                    ).format(prefix=ctx.prefix)
                )
                return

        realm = ("-".join(realm).lower() if isinstance(realm, tuple) else realm.lower()).replace(
            " ", "-"
        )
        region = region.lower()
        guild = guild.title()

        await ctx.defer()
        profile_data = await self.rio_guild_profile(
            region,
            realm,
            guild,
            fields=["raid_rankings", "raid_progression"],
        )

        try:
            guild_name: str = profile_data["name"]
        except KeyError:
            await ctx.send(
                _("The guild {guild} does not exist on {realm}.").format(
                    guild=guild, realm=realm
                )
            )
            return
        guild_url: Optional[str] = profile_data.get("profile_url")
        last_crawled_at = profile_data.get("last_crawled_at")
        last_updated: str = self.parse_date(last_crawled_at) if last_crawled_at else "N/A"

        # The current raid may be missing in the response (new tier, Fated raids,
        # classic without static data) -> read everything defensively.
        raid_slug = await self.get_current_raid(region) or self.best_raid_slug(
            profile_data.get("raid_progression")
        )
        rankings = (profile_data.get("raid_rankings") or {}).get(raid_slug) or {}
        diff_keys = [k for k in ("normal", "heroic", "mythic") if k in rankings] or list(rankings)
        ranks = tuple(rankings.get(k) or {} for k in diff_keys)
        difficulties = tuple(k.title() for k in diff_keys)

        raid_progression: str = self.raid_summary(profile_data.get("raid_progression"), raid_slug)

        embed = discord.Embed(title=guild_name, url=guild_url, color=0xFF2121)
        embed.set_author(
            name=_("Guild profile"),
            icon_url="https://cdnassets.raider.io/images/fb_app_image.jpg",
        )
        embed.add_field(name=_("__**Progress**__"), value=raid_progression, inline=False)

        for rank, difficulty in zip(ranks, difficulties):
            world = rank.get("world", "N/A")
            region = rank.get("region", "N/A")
            realm = rank.get("realm", "N/A")

            embed.add_field(
                name=_("{difficulty} rank").format(difficulty=difficulty),
                value=_("World: {world}\nRegion: {region}\nRealm: {realm}").format(
                    world=world, region=region, realm=realm
                ),
            )

        embed.set_footer(text=_("Last updated: {last_updated}").format(last_updated=last_updated))

        await ctx.send(embed=embed)

    # @raiderio_guild.autocomplete("realm")
    # async def raiderio_guild_realm_autocomplete(
    #     self, interaction: discord.Interaction, current: str
    # ) -> List[app_commands.Choice[str]]:
    #     realms = await get_realms(current)
    #     return realms[:25]

    @raiderio.command(name="affixes")
    @commands.guild_only()
    async def raiderio_affixes(self, ctx: commands.Context, region: str) -> None:
        """Display this week's affixes."""
        if ctx.interaction:
            # There is no contextual locale for interactions, so we need to set it manually
            # (This is probably a bug in Red, remove this when it's fixed)
            await set_contextual_locales_from_guild(self.bot, ctx.guild)

        if GAME == "classic":
            lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"
            await ctx.send(rio_not_available_msg(lang))
            return
        await ctx.defer()
        affixes = await self.raiderio_api.get_mythic_plus_affixes(region)
        affixes = affixes.get("affix_details") or []

        msg = ""
        if region == "eu":
            now = datetime.now(timezone.utc)
            reset_date = now + timedelta(days=(2 - now.weekday()) % 7)
            if now.date() == reset_date.date() and now.hour > 7:
                reset_date += timedelta(days=7)
            reset_date = reset_date.replace(
                hour=7, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
            )
            msg += _("\nThe weekly reset is {timestamp}.").format(
                timestamp=f"<t:{int(reset_date.timestamp())}:R>"
            )
        elif region == "us":
            now = datetime.now(timezone.utc)
            reset_date = now + timedelta(days=(1 - now.weekday()) % 7)
            if now.date() == reset_date.date() and now.hour > 15:
                reset_date += timedelta(days=7)
            reset_date = reset_date.replace(
                hour=15, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
            )
            msg += _("\nThe weekly reset is {timestamp}.").format(
                timestamp=f"<t:{int(reset_date.timestamp())}:R>"
            )
        # TODO: Find out when the reset is for KR and CN
        if ctx.channel.permissions_for(ctx.guild.me).embed_links:
            embed = discord.Embed(
                title=_("This week's Mythic+ affixes"),
                description=msg or None,
                color=await ctx.embed_color(),
            )
            embed.set_thumbnail(url="https://i.imgur.com/kczQ4Jt.jpg")
            for affix in affixes:
                embed.add_field(
                    name=affix["name"],
                    value=affix["description"],
                    inline=False,
                )
            await ctx.send(embed=embed)
        else:
            msg = _("This week's Mythic+ affixes:")
            for affix in affixes:
                msg += f"\n**{affix['name']}**: {affix['description']}"
            await ctx.send(msg)

    # @raiderio_affixes.autocomplete("region")
    # async def raiderio_affixes_region_autocomplete(
    #     self, interaction: discord.Interaction, current: str
    # ) -> List[app_commands.Choice[str]]:
    #     return [
    #         app_commands.Choice(name=region, value=region)
    #         for region in ["EU", "US"]
    #         if current.lower() in region.lower()
    #     ][:25]

    # ------------------------------------------------------------------ #
    # raider.io access (retail: raiderio-async, classic: classic.raider.io)
    # ------------------------------------------------------------------ #
    async def _classic_rio_get(self, path: str, params: dict) -> dict:
        try:
            async with self.session.get(
                f"{CLASSIC_RIO_BASE}{path}",
                params=params,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                js = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            log.debug("classic.raider.io request %s failed: %s", path, e)
            return {"error": "request_failed", "message": str(e)}
        # Errors come back as {"statusCode": 400, "error": ..., "message": ...}.
        return js if isinstance(js, dict) else {}

    async def rio_character_profile(
        self, region: str, realm: str, name: str, fields: Optional[list] = None
    ) -> dict:
        if GAME == "classic":
            # classic.raider.io documents gear, talents and guild; raid_progression is
            # answered as well (undocumented). Mythic+ fields do not exist there.
            allowed = ("gear", "talents", "guild", "raid_progression")
            params = {"region": region, "realm": realm, "name": name}
            wanted = [f for f in (fields or []) if f in allowed]
            if wanted:
                params["fields"] = ",".join(wanted)
            return await self._classic_rio_get("/api/v1/characters/profile", params)
        return await self.raiderio_api.get_character_profile(region, realm, name, fields=fields)

    async def rio_guild_profile(
        self, region: str, realm: str, name: str, fields: Optional[list] = None
    ) -> dict:
        if GAME == "classic":
            # Documented: members; raid_progression/raid_rankings are answered as well.
            allowed = ("members", "raid_progression", "raid_rankings")
            params = {"region": region, "realm": realm, "name": name}
            wanted = [f for f in (fields or []) if f in allowed]
            if wanted:
                params["fields"] = ",".join(wanted)
            return await self._classic_rio_get("/api/v1/guilds/profile", params)
        return await self.raiderio_api.get_guild_profile(region, realm, name, fields=fields)

    async def _rio_static_data(self, kind: str) -> dict:
        """raider.io static data per expansion id: {expansion_id: [raids|seasons]}.

        ``kind`` is "raiding" or "mythic-plus". Retail only; cached for 6 hours.
        """
        if GAME != "retail":
            return {}
        cache = getattr(self, "_rio_static_cache", None)
        if cache is None:
            cache = self._rio_static_cache = {}
        hit = cache.get(kind)
        if hit and time.time() - hit[0] < _RIO_STATIC_TTL:
            return hit[1]
        key = "raids" if kind == "raiding" else "seasons"
        out: dict = {}
        for eid in range(_RIO_MIN_EXPANSION_ID, _RIO_MIN_EXPANSION_ID + 6):
            try:
                if kind == "raiding":
                    js = await self.raiderio_api.get_raid_static_data(eid)
                else:
                    js = await self.raiderio_api.get_mythic_plus_static_data(eid)
            except Exception as e:
                log.debug("raider.io static data (%s, %s) failed: %s", kind, eid, e)
                break
            items = js.get(key) if isinstance(js, dict) else None
            if not items:
                # Unknown expansion id -> {"statusCode": 400, ...} or an empty list.
                break
            out[eid] = [i for i in items if isinstance(i, dict)]
        if out:
            cache[kind] = (time.time(), out)
        return out

    @staticmethod
    def _pick_current(items: list, region: str, *, main_only: bool = False) -> Optional[dict]:
        """Entry that has started in ``region`` most recently (ties: last in list)."""
        now = datetime.now(timezone.utc)
        best, best_start = None, None
        for it in items:
            if main_only and not it.get("is_main_season"):
                continue
            starts = it.get("starts") or {}
            start = _parse_ts(starts.get((region or "").lower()) or starts.get("us"))
            if start is None or start > now:
                continue
            if best_start is None or start >= best_start:
                best, best_start = it, start
        return best

    async def get_current_raid(self, region: str = "us") -> Optional[str]:
        """Slug of the current retail raid from raider.io static data (None if unknown)."""
        data = await self._rio_static_data("raiding")
        for eid in sorted(data, reverse=True):
            if picked := self._pick_current(data[eid], region):
                return picked.get("slug")
        return None

    async def get_current_mplus_season(self, region: str = "us") -> Optional[str]:
        """Slug of the current main Mythic+ season (e.g. "season-mn-2"), None if unknown."""
        data = await self._rio_static_data("mythic-plus")
        for eid in sorted(data, reverse=True):
            if picked := self._pick_current(data[eid], region, main_only=True):
                return picked.get("slug")
        return None

    @staticmethod
    def best_raid_slug(raid_progression: Optional[dict]) -> Optional[str]:
        """Fallback without a known current raid: raid with the most bosses killed."""
        best, best_kills = None, -1
        for slug, prog in (raid_progression or {}).items():
            if not isinstance(prog, dict):
                continue
            kills = sum(v for k, v in prog.items() if k.endswith("_bosses_killed") and isinstance(v, int))
            if kills > best_kills:
                best, best_kills = slug, kills
        return best

    @classmethod
    def raid_summary(cls, raid_progression: Optional[dict], raid_slug: Optional[str]) -> str:
        # Without a known current raid (classic, static data down) fall back to the
        # raid with the most kills; a known but missing raid stays "N/A".
        slug = raid_slug or cls.best_raid_slug(raid_progression)
        summary = ((raid_progression or {}).get(slug) or {}).get("summary") if slug else None
        return summary or "N/A"

    @staticmethod
    def parse_date(tz_date) -> str:
        parsed = isoparse(tz_date) + timedelta(hours=2)
        return parsed.strftime("%d/%m/%y - %H:%M:%S")

    @staticmethod
    async def make_gear_embed(  # TODO: Holy shit just use a class instead of all these args
        char_gear, char_image, char_last_updated, char_name, char_score_color, char_url
    ):
        char_gear = char_gear or {}
        item_list = [
            _("**Average ilvl:** {avg_ilvl}\n").format(
                avg_ilvl=char_gear.get("item_level_equipped", "N/A")
            )
        ]

        items = char_gear.get("items") or {}
        for item_slot, item in items.items():
            if not isinstance(item, dict):
                continue
            item_str = ""

            # Item rarity
            item_str += await Raiderio._get_item_quality(item)

            # Item level
            item_str += f" `{item.get('item_level', '?')}`"

            # Item name
            item_name = item.get("name", "?")
            item_id = item.get("item_id")
            if item_id:
                # Item link
                item_str += f" [{item_name}]({Raiderio._wowhead_url(item_id)})"
            else:
                item_str += f" {item_name}"

            item_list.append(item_str)
        if not items:
            item_list.append("N/A")

        embed = discord.Embed(
            title=char_name,
            url=char_url,
            description="\n".join(item_list),
            color=char_score_color,
        )
        embed.set_author(
            name=_("classic.raider.io profile"),
            icon_url="https://cdnassets.raider.io/images/fb_app_image.jpg",
        )
        embed.set_thumbnail(url=char_image)
        embed.set_footer(
            text=_("Last updated: {char_last_updated}").format(char_last_updated=char_last_updated)
        )
        return embed

    @staticmethod
    async def _get_item_quality(item) -> str:
        """
        Get the item quality emoji.

        :param item: The item to get the quality emoji for.
        :return: The item quality emoji.
        """
        quality = item.get("item_quality")
        if quality == 1:
            return "⬜"
        elif quality == 2:
            return "🟩"
        elif quality == 3:
            return "🟦"
        elif quality == 4:
            return "🟪"
        elif quality == 5:
            return "🟧"
        else:
            return "⬛"

    @staticmethod
    def _wowhead_url(item_id):
        """
        Returns a Wowhead URL for the given item ID.

        :param item_id: ID of the item
        :return: Wowhead URL
        """
        return f"https://www.wowhead.com/mop-classic/item={item_id}"


class ProfileMenu(SimpleMenu):
    def __init__(
        self,
        pages: List[_ACCEPTABLE_PAGE_TYPES],
        talents: str,
        timeout: float = 180.0,
        page_start: int = 0,
        delete_after_timeout: bool = False,
        disable_after_timeout: bool = False,
        use_select_menu: bool = False,
        use_select_only: bool = False,
    ) -> None:
        super().__init__(
            pages,
            timeout=timeout,
            page_start=page_start,
            delete_after_timeout=delete_after_timeout,
            disable_after_timeout=disable_after_timeout,
            use_select_menu=use_select_menu,
            use_select_only=use_select_only,
        )
        self.talents = talents

        talents_button = discord.ui.Button(
            label=_("Talents"),
            style=discord.ButtonStyle.link,
            row=1,
            url=self.get_talent_calc_url(),
        )
        self.add_item(talents_button)

    def get_talent_calc_url(self):
        return f"https://www.wowhead.com/mop-classic/talent-calc/blizzard/{self.talents}"

    async def on_timeout(self):
        if self.delete_after_timeout and not self.message.flags.ephemeral:
            await self.message.delete()
        elif self.disable_after_timeout:
            for child in self.children:
                if child.row == 1:  # Don't disable the talents button
                    continue
                child.disabled = True
            await self.message.edit(view=self)
        else:
            await self.message.edit(view=None)

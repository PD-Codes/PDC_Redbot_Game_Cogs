import itertools
import logging
import re
from io import BytesIO
from typing import List, Optional

import aiohttp
import discord
from PIL import Image
from redbot.core import commands

from .exceptions import InvalidBlizzardAPI
from .forever_api import ForeverAPIError, api_get, wowhead_url
from .pdc_dashboard import tr_lang

log = logging.getLogger("red.karlo-cogs.wowtools_forever")

# Fallback region for the static game data lookups ([[spell]] / [[item]] mentions)
# when the server has no region configured.
_ON_MESSAGE_REGION = "us"


# Sibling cogs that also answer [[...]] mentions when their on_message is enabled.
_OTHER_ONMESSAGE_COGS = ("WoWTools", "WoWToolsClassic")


class OnMessage:
    async def other_onmessage_cogs(self, guild: discord.Guild) -> List[str]:
        """Names of loaded sibling wowtools cogs that have on_message enabled in ``guild``."""
        active: List[str] = []
        for cog_name in _OTHER_ONMESSAGE_COGS:
            cog = self.bot.get_cog(cog_name)
            if cog is None or cog is self:
                continue
            try:
                if await cog.config.guild(guild).on_message():
                    active.append(cog_name)
            except Exception:
                continue
        return active

    async def onmessage_conflict_hint(self, guild: discord.Guild, lang: str) -> str:
        """Bilingual warning if another wowtools cog also answers [[...]] in ``guild`` ("" if none)."""
        others = await self.other_onmessage_cogs(guild)
        if not others:
            return ""
        names = ", ".join(others)
        return tr_lang(
            lang,
            f"Hinweis: {names} beantwortet [[...]] auf diesem Server ebenfalls. "
            "Deaktiviere dort on_message, sonst kommen doppelte Antworten.",
            f"Note: {names} also answers [[...]] on this server. "
            "Disable on_message there, otherwise replies will be duplicated.",
        )

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if not await self.is_valid(message):
            return
        if not await self.config.guild(message.guild).on_message():
            return

        search_strings = self.extract_search_string(message.content)
        if not search_strings:
            return

        embeds = []
        try:
            region = await self.config.guild(message.guild).region() or _ON_MESSAGE_REGION
            embeds = await self.get_embeds(search_strings, region)
        except InvalidBlizzardAPI:
            log.warning(
                "The Blizzard API is not properly set up.\n"
                "Create a client on https://develop.battle.net/ and then type in "
                "`{prefix}set api blizzard client_id,whoops client_secret,whoops` "
                "filling in `whoops` with your client's ID and secret."
            )
        if not embeds:
            return

        await message.channel.send(embeds=embeds)

    async def is_valid(self, message: discord.Message) -> bool:
        # check whether the message was sent in a guild
        if message.guild is None:
            return False
        # check whether the message author isn't a bot
        if message.author.bot:
            return False
        # webhook posts and authors missing from the member cache arrive as plain User objects;
        # Red's ignore check needs a Member and raises AttributeError ('_roles') otherwise
        if message.webhook_id is not None or not isinstance(message.author, discord.Member):
            return False
        # check whether the bot can send and delete messages in the given channel
        channel_perms = message.channel.permissions_for(message.guild.me)
        if not channel_perms.send_messages:
            return False
        # check whether the cog isn't disabled
        if await self.bot.cog_disabled_in_guild(self, message.guild):
            return False
        # check whether the channel isn't on the ignore list
        if not await self.bot.ignored_channel_or_guild(message):
            return False
        # check whether the message author isn't on allowlist/blocklist
        if not await self.bot.allowed_by_whitelist_blacklist(message.author):
            return False
        return True

    @staticmethod
    def extract_search_string(string: str) -> List[str]:
        pattern = r"\[\[(.*?)\]\]"
        return re.findall(pattern, string)

    async def get_embeds(
        self, search_strings: List[str], region: str = _ON_MESSAGE_REGION
    ) -> List[discord.Embed]:
        embeds = []
        async with aiohttp.ClientSession() as session:
            for search_string in search_strings[:5]:
                await self.search_for_string((session, region), embeds, search_string)
        return embeds

    async def _static_get(self, session, path: str, params: Optional[dict] = None) -> dict:
        # Static game data of WoW Forever (static-forever-*, versioned fallback on 403).
        # ``session`` is a (aiohttp session, region) tuple.
        http, region = session
        await self.limiter.acquire()
        return await api_get(self, region, path, kind="static", params=params, session=http)

    async def search_for_string(self, session, embeds, search_string):
        search_params = {
            "name.en_US": search_string,
            "orderby": "id",
            "_page": 1,
            "_pageSize": 1000,
        }
        for obj_type in ("spell", "item"):
            try:
                search_results = await self._static_get(
                    session, f"/data/wow/search/{obj_type}", search_params
                )
            except InvalidBlizzardAPI:
                raise
            except (ForeverAPIError, aiohttp.ClientError):
                continue

            for result in search_results.get("results") or []:
                data = result.get("data") or {}
                result_name = (data.get("name") or {}).get("en_US") or ""
                if result_name.lower() != search_string.lower():
                    continue
                result_id = data.get("id")
                if result_id is None:
                    continue
                try:
                    embed = await self.get_or_fetch_embed(session, result_name, result_id, obj_type)
                except (ForeverAPIError, aiohttp.ClientError):
                    break
                embeds.append(embed)
                break

    async def get_or_fetch_embed(self, session, result_name, result_id, obj_type):
        cache_key = f"{obj_type}:{result_id}"
        if self.on_message_cache.get(cache_key):
            return self.on_message_cache[cache_key]
        embed = await self.make_embed(session, result_name, result_id, obj_type)
        self.on_message_cache[cache_key] = embed
        return embed

    async def make_embed(self, session, result_name, result_id, obj_type):
        result_description = await self._static_get(session, f"/data/wow/{obj_type}/{result_id}")
        try:
            result_icon = await self._static_get(session, f"/data/wow/media/{obj_type}/{result_id}")
            icon_url = ((result_icon.get("assets") or [{}])[0]).get("value")
        except ForeverAPIError:
            icon_url = None
        if obj_type == "spell" and icon_url:
            colour = await self.get_spell_colour(icon_url)
        elif obj_type == "spell":
            colour = discord.Color.blurple()
        else:
            colour = self.get_item_rarity_color(result_description.get("quality"))
        embed = discord.Embed(
            title=result_name,
            description=self.generate_description(result_description, obj_type),
            url=wowhead_url(obj_type, result_id),
            colour=colour,
        )
        if icon_url:
            embed.set_thumbnail(url=icon_url)
        return embed

    def generate_description(self, description, obj_type):
        if obj_type == "spell":
            return description.get("description")
        if obj_type == "item" and description.get("preview_item"):
            # Forever item data may lack fields -> only use .get() on nested nodes.
            preview = description.get("preview_item") or {}
            generated_str = ""
            level = (preview.get("level") or {}).get("display_string")
            if level:
                generated_str += f"**{level}**"
            binding = (preview.get("binding") or {}).get("name")
            if binding:
                generated_str += f"\n{binding}"

            subclass = (preview.get("item_subclass") or {}).get("name")
            if subclass:
                generated_str += f"\n{subclass}"
            inv_type = (preview.get("inventory_type") or {}).get("name")
            if inv_type:
                generated_str += f" - {inv_type}\n"

            weapon = preview.get("weapon") or {}
            damage = (weapon.get("damage") or {}).get("display_string")
            dps = (weapon.get("dps") or {}).get("display_string")
            if damage:
                generated_str += f"{damage}\n"
            if dps:
                generated_str += f"{dps}\n"

            for stat in preview.get("stats") or []:
                stat_str = ((stat or {}).get("display") or {}).get("display_string")
                if stat_str:
                    generated_str += f"\n{stat_str}"

            spell_parts = []
            for spell in preview.get("spells") or []:
                spell = spell or {}
                spell_name = (spell.get("spell") or {}).get("name") or ""
                spell_desc = spell.get("description") or ""
                if spell_name or spell_desc:
                    spell_parts.append(f"\n\n**{spell_name}**\n{spell_desc}")
            if spell_parts:
                generated_str += "\n".join(spell_parts)

            req_level = ((preview.get("requirements") or {}).get("level") or {}).get("display_string")
            if req_level:
                generated_str += f"\n\n{req_level}"
            return generated_str

    async def get_spell_colour(self, url: str) -> discord.Color:
        """Get the average colour of an image by averaging the RGB values of all pixels."""
        async with self.session.get(url) as response:
            img = BytesIO(await response.read())
        img.seek(0)

        # Icons can be palette/RGBA images; convert so getpixel() always yields (r, g, b).
        image = Image.open(img).convert("RGB")
        width, height = image.size

        total_r = total_g = total_b = 0
        for x, y in itertools.product(range(width), range(height)):
            r, g, b = image.getpixel((x, y))
            total_r += r
            total_g += g
            total_b += b

        pixels = width * height
        avg_r = total_r // pixels
        avg_g = total_g // pixels
        avg_b = total_b // pixels
        return discord.Color.from_rgb(avg_r, avg_g, avg_b)

    def get_item_rarity_color(self, rarity: Optional[dict]) -> discord.Color:
        if not rarity or not rarity.get("type"):
            return discord.Color.blurple()
        else:
            rarity = rarity["type"]
        if rarity.lower() == "poor":
            return discord.Color.from_str("#9d9d9d")
        if rarity.lower() == "common":
            return discord.Color.from_str("#ffffff")
        if rarity.lower() == "uncommon":
            return discord.Color.from_str("#1eff00")
        if rarity.lower() == "rare":
            return discord.Color.from_str("#0070dd")
        if rarity.lower() == "epic":
            return discord.Color.from_str("#a335ee")
        if rarity.lower() == "legendary":
            return discord.Color.from_str("#ff8000")
        if rarity.lower() == "artifact":
            return discord.Color.from_str("#e6cc80	")
        if rarity.lower() == "heirloom":
            return discord.Color.from_str("#00ccff")
        if rarity.lower() == "wow_token":
            return discord.Color.from_str("#00ccff")
        return discord.Color.blurple()

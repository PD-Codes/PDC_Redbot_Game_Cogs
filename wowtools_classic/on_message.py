import asyncio
import itertools
import logging
import re
from io import BytesIO
from typing import List, Optional

import aiohttp
import discord
from PIL import Image
from redbot.core import commands

from .blizzard_rest import BlizzardAPIError, api_get
from .exceptions import InvalidBlizzardAPI

log = logging.getLogger("red.karlo-cogs.wowtools")

# Region used for the [[...]] lookups (game data is the same in all regions).
_ON_MESSAGE_REGION = "us"
_MAX_EMBED_DESC = 4000


class OnMessage:
    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if not await self.is_valid(message):
            return
        if not await self.config.guild(message.guild).on_message():
            return

        search_strings = self.extract_search_string(message.content)
        if not search_strings:
            return

        embeds: List[discord.Embed] = []
        try:
            embeds = await self.get_embeds(search_strings)
        except InvalidBlizzardAPI:
            log.warning(
                "The Blizzard API is not properly set up.\n"
                "Create a client on https://develop.battle.net/ and then type in "
                "`{prefix}set api blizzard client_id,whoops client_secret,whoops` "
                "filling in `whoops` with your client's ID and secret."
            )
            return
        except Exception as e:
            log.warning("[[...]] lookup failed: %s", e)
            return
        if not embeds:
            return

        await message.channel.send(embeds=embeds[:10])

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

    async def get_embeds(self, search_strings: List[str]) -> List[discord.Embed]:
        embeds = []
        for search_string in search_strings[:5]:
            await self.search_for_string(embeds, search_string)

        return embeds

    async def search_for_string(self, embeds, search_string):
        # Plain REST calls in this cog's static namespace (static-{r} / static-classic-{r});
        # aiowowapi 2.1.5 has no spell search for Classic.
        search_params = {
            "name.en_US": search_string,
            "orderby": "id",
            "_page": 1,
            "_pageSize": 1000,
        }
        for obj_type in ("spell", "item"):
            try:
                await self.limiter.acquire()
                search_results = await api_get(
                    self,
                    _ON_MESSAGE_REGION,
                    f"/data/wow/search/{obj_type}",
                    kind="static",
                    locale=None,
                    params=search_params,
                )
            except BlizzardAPIError as e:
                # 403/404: this search does not exist in the namespace (e.g. classic
                # spell search) -> silently skip, the auto-reply must not spam errors.
                log.debug("%s search not available (%s)", obj_type, e)
                continue
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.debug("%s search failed: %s", obj_type, e)
                continue

            for result in search_results.get("results") or []:
                data = (result or {}).get("data") or {}
                name = data.get("name")
                if isinstance(name, dict):
                    name = name.get("en_US")
                if not isinstance(name, str) or name.lower() != search_string.lower():
                    continue
                result_id = data.get("id")
                if result_id is None:
                    continue
                embed = await self.get_or_fetch_embed(result, result_id, obj_type)
                if embed is not None:
                    embeds.append(embed)
                break

    async def get_or_fetch_embed(self, result, result_id, obj_type):
        # Spell and item ids overlap -> cache per type.
        cache_key = f"{obj_type}:{result_id}"
        if self.on_message_cache.get(cache_key):
            return self.on_message_cache.get(cache_key)
        embed = await self.make_embed(result, obj_type)
        if embed is not None:
            self.on_message_cache[cache_key] = embed
        return embed

    async def make_embed(self, result, obj_type):
        obj_id = result["data"]["id"]
        try:
            result_description = await api_get(
                self, _ON_MESSAGE_REGION, f"/data/wow/{obj_type}/{obj_id}", kind="static"
            )
        except BlizzardAPIError as e:
            log.debug("%s %s not available: %s", obj_type, obj_id, e)
            return None
        try:
            result_icon = await api_get(
                self, _ON_MESSAGE_REGION, f"/data/wow/media/{obj_type}/{obj_id}", kind="static"
            )
        except BlizzardAPIError:
            result_icon = {}
        assets = [a for a in result_icon.get("assets") or [] if isinstance(a, dict)]
        icon_url = assets[0].get("value") if assets else None

        name = result["data"].get("name")
        title = name.get("en_US") if isinstance(name, dict) else name
        if obj_type == "spell":
            colour = await self.get_spell_colour(icon_url) if icon_url else discord.Color.blurple()
        else:
            colour = self.get_item_rarity_color(result_description.get("quality"))
        description = self.generate_description(result_description, obj_type) or ""
        embed = discord.Embed(
            title=title or "?",
            description=description[:_MAX_EMBED_DESC],
            url=f"https://www.wowhead.com/mop-classic/{obj_type}={result['data']['id']}",
            colour=colour,
        )
        if icon_url:
            embed.set_thumbnail(url=icon_url)
        return embed

    @staticmethod
    def _display(node) -> str:
        """``display_string`` of a nested node, "" if missing."""
        if isinstance(node, dict):
            value = node.get("display_string")
            if isinstance(value, str):
                return value
        return ""

    def generate_description(self, description, obj_type):
        description = description or {}
        if obj_type == "spell":
            return description.get("description") or ""
        if obj_type == "item" and description.get("preview_item"):
            preview = description["preview_item"]
            generated_str = ""
            if preview.get("level"):
                generated_str += f"**{self._display(preview['level'])}**"
            if preview.get("binding"):
                generated_str += f"\n{(preview['binding'] or {}).get('name', '')}"

            # if preview.get("item_class"):
            #     generated_str += f"\n{preview['item_class']['name']}"
            if preview.get("item_subclass"):
                generated_str += f"\n{(preview['item_subclass'] or {}).get('name', '')}"
            if preview.get("inventory_type"):
                generated_str += f" - {(preview['inventory_type'] or {}).get('name', '')}\n"

            weapon = preview.get("weapon") or {}
            if weapon:
                generated_str += (
                    f"{self._display(weapon.get('damage'))}\n"
                    f"{self._display(weapon.get('dps'))}\n"
                )

            for stat in preview.get("stats") or []:
                generated_str += f"\n{self._display((stat or {}).get('display'))}"

            if preview.get("spells"):
                generated_str += "\n".join(
                    [
                        f"\n\n**{((spell or {}).get('spell') or {}).get('name', '?')}**\n"
                        f"{(spell or {}).get('description', '')}"
                        for spell in preview["spells"]
                    ]
                )

            requirements = preview.get("requirements") or {}
            if requirements.get("level"):
                generated_str += f"\n\n{self._display(requirements['level'])}"
            return generated_str
        return ""

    async def get_spell_colour(self, url: str) -> discord.Color:
        """Get the average colour of an image by averaging the RGB values of all pixels."""
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return discord.Color.blurple()
                img = BytesIO(await response.read())
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return discord.Color.blurple()
        img.seek(0)

        try:
            # Icons can be RGBA/P images -> getpixel would not return 3 values.
            image = Image.open(img).convert("RGB")
        except Exception:
            return discord.Color.blurple()
        width, height = image.size

        total_r = total_g = total_b = 0
        for x, y in itertools.product(range(width), range(height)):
            r, g, b = image.getpixel((x, y))
            total_r += r
            total_g += g
            total_b += b

        pixels = width * height
        if not pixels:
            return discord.Color.blurple()
        avg_r = total_r // pixels
        avg_g = total_g // pixels
        avg_b = total_b // pixels
        return discord.Color.from_rgb(avg_r, avg_g, avg_b)

    def get_item_rarity_color(self, rarity: Optional[dict]) -> discord.Color:
        if not rarity:
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

import io
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

import discord
from discord.ext import tasks
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont
from redbot.core import commands
from redbot.core.data_manager import bundled_data_path
from redbot.core.i18n import Translator, set_contextual_locales_from_guild
from redbot.core.utils.chat_formatting import box, humanize_list, humanize_number
from tabulate import tabulate

from .autocomplete import GAME
from .blizzard_rest import BlizzardAPIError, fetch_character, fetch_guild_roster
from .pdc_dashboard import tr_lang

log = logging.getLogger("red.karlo-cogs.wowtools")
_ = Translator("WoWTools", __file__)

DEV_GUILDS = [362298824854863882, 133049272517001216]


class Scoreboard:
    async def _scoreboard_unavailable(self, ctx: commands.Context) -> bool:
        """The scoreboard is built from raider.io Mythic+ data, which MoP Classic lacks."""
        if GAME != "classic":
            return False
        lang = await self.config.guild(ctx.guild).language() if ctx.guild else "en-US"
        await ctx.send(
            tr_lang(
                lang,
                "Das Mythic+-Scoreboard ist für MoP Classic nicht verfügbar.",
                "The Mythic+ scoreboard is not available for MoP Classic.",
            ),
            ephemeral=True,
        )
        return True

    @commands.cooldown(rate=1, per=10, type=commands.BucketType.user)
    @commands.hybrid_group(name="wowtc-wowscoreboard", aliases=["wowtc-sb"])
    @commands.guild_only()
    async def wowscoreboard(self, ctx: commands.Context):
        """Show various scoreboards for your guild."""
        pass

    @wowscoreboard.command(name="dungeon")
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def wowscoreboard_dungeon(self, ctx: commands.Context):
        """Get the Mythic+ scoreboard for this guild."""
        if ctx.interaction:
            # There is no contextual locale for interactions, so we need to set it manually
            # (This is probably a bug in Red, remove this when it's fixed)
            await set_contextual_locales_from_guild(self.bot, ctx.guild)
        if await self._scoreboard_unavailable(ctx):
            return

        # Defer right away: the raider.io roster call can exceed the 3s interaction window.
        await ctx.defer()
        image_enabled = await self.config.guild(ctx.guild).sb_image()
        try:
            if image_enabled:
                embed, img_file = await self._generate_dungeon_scoreboard(ctx, True)
            else:
                embed = await self._generate_dungeon_scoreboard(ctx)
        except Exception as e:
            await ctx.send(_("Command failed successfully. {e}").format(e=e), ephemeral=True)
            return
        if embed:
            if image_enabled and ctx.channel.permissions_for(ctx.guild.me).attach_files:
                await ctx.send(embed=embed, file=img_file)
            else:
                await ctx.send(embed=embed)

    # @commands.cooldown(rate=1, per=60, type=commands.BucketType.guild)
    # @wowscoreboard.command(name="pvp", hidden=True)
    # @commands.guild_only()
    # async def wowscoreboard_pvp(self, ctx: commands.Context):
    #     """Get all the PvP related scoreboards for this guild.

    #     **Characters that have not played all PvP gamemodes at
    #     some point will not be shown.**
    #     """
    #     if ctx.interaction:
    #         # There is no contextual locale for interactions, so we need to set it manually
    #         # (This is probably a bug in Red, remove this when it's fixed)
    #         await set_contextual_locales_from_guild(self.bot, ctx.guild)

    #     try:
    #         embed = await self._generate_pvp_scoreboard(ctx)
    #     except InvalidBlizzardAPI:
    #         await ctx.send(_("Blizzard API not properly set up."))
    #         return
    #     if embed:
    #         # TODO: In dpy2, make this a list of embeds to send in a single message
    #         await ctx.send(embed=embed)

    @commands.hybrid_group(name="wowtc-sbset")
    @commands.admin()
    @commands.guild_only()
    async def sbset(self, ctx):
        """Change Mythic+ leaderboard settings"""
        pass

    @sbset.command(name="channel")
    @commands.admin()
    @commands.guild_only()
    async def sbset_channel(
        self, ctx: commands.Context, channel: discord.TextChannel | None = None
    ):
        """Set the channel to send the Mythic+ leaderboard to."""
        if await self._scoreboard_unavailable(ctx):
            return
        if ctx.interaction:
            await ctx.defer(ephemeral=True)
        image_enabled = await self.config.guild(ctx.guild).sb_image()
        sb_channel_id: int = await self.config.guild(ctx.guild).scoreboard_channel()
        sb_msg_id: int = await self.config.guild(ctx.guild).scoreboard_message()
        if not channel:
            if sb_msg_id:  # Remove the scoreboard message if it exists
                await self._delete_scoreboard(
                    ctx,
                    sb_channel_id,
                    sb_msg_id,
                )
            await self.config.guild(ctx.guild).scoreboard_channel.clear()
            await self.config.guild(ctx.guild).scoreboard_message.clear()
            await ctx.send(_("Scoreboard channel cleared."))
            return
        if (
            not channel.permissions_for(ctx.guild.me).send_messages
            or not channel.permissions_for(ctx.guild.me).embed_links
        ):
            await ctx.send(
                _(
                    "I need the `Send Messages` and `Embed Links` permissions to post the scoreboard to {channel}".format(
                        channel=channel.mention
                    )
                )
            )
            return
        if sb_msg_id:  # Remove the old scoreboard
            await self._delete_scoreboard(
                ctx,
                sb_channel_id,
                sb_msg_id,
            )
        await self.config.guild(ctx.guild).scoreboard_channel.set(channel.id)
        try:
            if image_enabled and channel.permissions_for(ctx.guild.me).attach_files:
                embed, img_file = await self._generate_dungeon_scoreboard(ctx, image=True)
            else:
                embed = await self._generate_dungeon_scoreboard(ctx)
        except Exception as e:
            await ctx.send(_("Command failed successfully. {e}").format(e=e), ephemeral=True)
            return
        if image_enabled and channel.permissions_for(ctx.guild.me).attach_files:
            sb_msg = await channel.send(file=img_file, embed=embed)
        else:
            sb_msg = await channel.send(embed=embed)
        await self.config.guild(ctx.guild).scoreboard_message.set(sb_msg.id)
        await ctx.send(_("Scoreboard channel set."))

    @sbset.group(name="blacklist", aliases=["blocklist"])
    @commands.admin()
    @commands.guild_only()
    async def sbset_blacklist(self, ctx: commands.Context):
        """Manage the scoreboard blacklist."""
        pass

    @sbset_blacklist.command(name="add")
    @commands.admin()
    @commands.guild_only()
    async def sbset_blacklist_add(self, ctx: commands.Context, *, characters: str):
        """Add characters to the scoreboard blacklist.

        Characters can be separated by spaces or commas.
        """
        blacklist: List[str] = await self.config.guild(ctx.guild).scoreboard_blacklist()
        for character in characters.split(" "):
            character = character.strip(",")
            if character not in blacklist:
                blacklist.append(character.lower())
        await self.config.guild(ctx.guild).scoreboard_blacklist.set(blacklist)
        await ctx.send(_("Blacklisted characters added."))

    @sbset_blacklist.command(name="remove")
    @commands.admin()
    @commands.guild_only()
    async def sbset_blacklist_remove(self, ctx: commands.Context, *, characters: str):
        """Remove characters from the scoreboard blacklist.

        Characters can be separated by spaces or commas.
        """
        blacklist: List[str] = await self.config.guild(ctx.guild).scoreboard_blacklist()
        for character in characters.split(" "):
            character = character.strip(",")
            character = character.lower()
            if character in blacklist:
                blacklist.remove(character.lower())
        await self.config.guild(ctx.guild).scoreboard_blacklist.set(blacklist)
        await ctx.send(_("Blacklisted characters removed."))

    @sbset_blacklist.command(name="list")
    @commands.admin()
    @commands.guild_only()
    async def sbset_blacklist_list(self, ctx: commands.Context):
        """List the characters on the scoreboard blacklist."""
        blacklist: List[str] = await self.config.guild(ctx.guild).scoreboard_blacklist()
        if not blacklist:
            await ctx.send(_("No characters are blacklisted."))
            return
        await ctx.send(
            _("Blacklisted characters: {characters}").format(characters=humanize_list(blacklist))
        )

    @sbset_blacklist.command(name="clear")
    @commands.admin()
    @commands.guild_only()
    async def sbset_blacklist_clear(self, ctx: commands.Context):
        """Clear the scoreboard blacklist."""
        await self.config.guild(ctx.guild).scoreboard_blacklist.clear()
        await ctx.send(_("Blacklisted characters cleared."))

    @sbset.command(name="lock", hidden=True)
    @commands.admin()
    @commands.guild_only()
    async def sbset_lock(self, ctx: commands.Context):
        """Lock the current scoreboard and do not update it anymore."""
        if await self._scoreboard_unavailable(ctx):
            return
        sb_channel_id: int = await self.config.guild(ctx.guild).scoreboard_channel()
        sb_msg_id: int = await self.config.guild(ctx.guild).scoreboard_message()
        if not (sb_channel_id and sb_msg_id):
            await ctx.send(_("No scoreboard is set."), ephemeral=True)
            return

        sb_channel = ctx.guild.get_channel(sb_channel_id)
        try:
            sb_msg: discord.Message = await sb_channel.fetch_message(sb_msg_id)
        except discord.HTTPException:
            log.error(
                f"Failed to fetch scoreboard message in guild {ctx.guild.id} ({ctx.guild.name}).",
                exc_info=True,
            )
            return
        if not sb_msg:
            await ctx.send(_("Scoreboard message not found"), ephemeral=True)
            return

        max_chars = 20
        headers = ["#", _("Name"), _("Score")]
        region: str = await self.config.guild(ctx.guild).region()
        realm: str = await self.config.guild(ctx.guild).realm()
        guild_name: str = await self.config.guild(ctx.guild).real_guild_name()
        sb_blacklist: List[str] = await self.config.guild(ctx.guild).scoreboard_blacklist()
        if not region or not realm or not guild_name:
            return
        image: bool = await self.config.guild(ctx.guild).sb_image()

        embed = discord.Embed(
            title=_("2nd War Within Season Scoreboard"),
            color=await self.bot.get_embed_color(sb_msg),
        )
        # ctx.guild.icon can be None (no icon set).
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        try:
            tabulate_list = await self._get_dungeon_scores(
                guild_name,
                max_chars,
                realm,
                region,
                sb_blacklist,
                image=image,
            )
        except ValueError as e:
            log.error(f"Error getting dungeon scores for {ctx.guild.id}, skipping. Response: {e}")
            return

        # TODO: When dpy2 is out, use discord.utils.format_dt()
        desc = _("Season 2 of War Within is over.\n").format(
            timestamp=int(datetime.now(timezone.utc).timestamp())
        )
        desc += _("Score cutoff for the season title was: `{cutoff}`\n").format(
            cutoff=await self.get_season_title_cutoff(region)
        )

        if image:
            img_file = await self._generate_scoreboard_image(
                tabulate_list, dev_guild=ctx.guild.id in DEV_GUILDS
            )
            embed.set_image(url=f"attachment://{img_file.filename}")
        else:
            formatted_rankings = box(
                tabulate(
                    tabulate_list,
                    headers=headers,
                    tablefmt="plain",
                    disable_numparse=True,
                ),
                lang="md",
            )
            desc += formatted_rankings

        ass_integration = await self.config.assistant_cog_integration()
        if (assistant := self.bot.get_cog("Assistant")) and ass_integration:
            await self.add_assistant_embedding(assistant, ctx.guild, image, tabulate_list)

        embed.description = desc

        try:
            if image:
                await sb_msg.edit(embed=embed, attachments=[img_file])
            else:
                await sb_msg.edit(embed=embed, attachments=[])
        except discord.Forbidden:
            log.error(
                f"Failed to edit scoreboard message in guild {ctx.guild.id} ({ctx.guild.name}) "
                f"due to missing permissions.",
                exc_info=True,
            )
        except discord.HTTPException:
            log.error(
                f"Failed to edit scoreboard message in guild {ctx.guild.id} ({ctx.guild.name}).",
                exc_info=True,
            )

        await self.config.guild(ctx.guild).scoreboard_channel.clear()
        await self.config.guild(ctx.guild).scoreboard_message.clear()
        await ctx.send(_("Scoreboard locked."))

    @tasks.loop(minutes=5)
    async def update_dungeon_scoreboard(self):
        for guild in self.bot.guilds:
            # Any per-guild failure must not kill the whole task loop.
            try:
                if await self.bot.cog_disabled_in_guild(self, guild):
                    continue
                await set_contextual_locales_from_guild(self.bot, guild)

                sb_channel_id: int = await self.config.guild(guild).scoreboard_channel()
                sb_msg_id: int = await self.config.guild(guild).scoreboard_message()
                if not (sb_channel_id and sb_msg_id):
                    continue
                sb_channel: discord.TextChannel = guild.get_channel(sb_channel_id)
                if sb_channel is None:
                    # The configured channel no longer exists; skip this guild.
                    log.warning(
                        f"Scoreboard channel {sb_channel_id} not found in guild {guild.id}."
                    )
                    continue

                try:
                    sb_msg: discord.Message = await sb_channel.fetch_message(sb_msg_id)
                except discord.HTTPException:
                    log.error(
                        f"Failed to fetch scoreboard message in guild {guild.id} ({guild.name}).",
                        exc_info=True,
                    )
                    continue
                if not sb_msg:
                    continue

                max_chars = 20
                headers = ["#", _("Name"), _("Score")]
                region: str = await self.config.guild(guild).region()
                realm: str = await self.config.guild(guild).realm()
                guild_name: str = await self.config.guild(guild).real_guild_name()
                sb_blacklist: List[str] = await self.config.guild(guild).scoreboard_blacklist()
                if not region or not realm or not guild_name:
                    continue
                image: bool = await self.config.guild(guild).sb_image()

                embed = discord.Embed(
                    title=_("Mythic+ Guild Scoreboard"),
                    color=await self.bot.get_embed_color(sb_msg),
                )
                # guild.icon can be None (no icon set).
                embed.set_author(
                    name=guild.name, icon_url=guild.icon.url if guild.icon else None
                )
                try:
                    tabulate_list = await self._get_dungeon_scores(
                        guild_name,
                        max_chars,
                        realm,
                        region,
                        sb_blacklist,
                        image=image,
                    )
                except ValueError as e:
                    log.error(
                        f"Error getting dungeon scores for {guild.id}, skipping. Response: {e}"
                    )
                    continue

                # TODO: When dpy2 is out, use discord.utils.format_dt()
                desc = _("Last updated <t:{timestamp}:R>\n").format(
                    timestamp=int(datetime.now(timezone.utc).timestamp())
                )
                if cutoff := await self.get_season_title_cutoff(region):
                    desc += _("Score cutoff for season title: `{cutoff}`\n").format(cutoff=cutoff)

                if image:
                    img_file = await self._generate_scoreboard_image(
                        tabulate_list, dev_guild=guild.id in DEV_GUILDS
                    )
                    embed.set_image(url=f"attachment://{img_file.filename}")
                    embed.set_footer(text=_("Updates every 5 minutes"))
                else:
                    formatted_rankings = box(
                        tabulate(
                            tabulate_list,
                            headers=headers,
                            tablefmt="plain",
                            disable_numparse=True,
                        ),
                        lang="md",
                    )
                    desc += formatted_rankings

                    # Don't edit if there wouldn't be a change.
                    # The message may have no embeds (e.g. stripped by another bot),
                    # in that case simply rebuild it below.
                    if sb_msg.embeds and sb_msg.embeds[0].description:
                        old_rankings = sb_msg.embeds[0].description.splitlines()
                        old_rankings = "\n".join(old_rankings[1:])
                        if old_rankings == formatted_rankings:
                            continue
                    embed.set_footer(text=_("Updates only when there is a ranking change"))

                ass_integration = await self.config.assistant_cog_integration()
                if (assistant := self.bot.get_cog("Assistant")) and ass_integration:
                    await self.add_assistant_embedding(assistant, guild, image, tabulate_list)

                embed.description = desc

                try:
                    if image:
                        await sb_msg.edit(embed=embed, attachments=[img_file])
                    else:
                        await sb_msg.edit(embed=embed, attachments=[])
                except discord.Forbidden:
                    log.error(
                        f"Failed to edit scoreboard message in guild {guild.id} ({guild.name}) "
                        f"due to missing permissions.",
                        exc_info=True,
                    )
                except discord.HTTPException:
                    log.error(
                        f"Failed to edit scoreboard message in guild {guild.id} ({guild.name}).",
                        exc_info=True,
                    )
            except Exception as e:
                log.exception("Scoreboard update failed for guild %s: %s", guild.id, e)
                continue

    @staticmethod
    async def add_assistant_embedding(assistant, guild, image, tabulate_list):
        if image:
            formatted_tab_list = [char[:3] + [char[-1]] for char in tabulate_list]
        headers = ["#", " | Name", " | Score", " | Item level"]
        formatted_rankings = (
            "This is a Mythic+ Guild Scoreboard, generated by you. " "It is sorted by score.\n\n"
        )
        formatted_rankings += tabulate(
            formatted_tab_list if image else tabulate_list,
            headers=headers,
            tablefmt="plain",
            disable_numparse=True,
        )
        try:
            # Unique per game so retail and classic do not overwrite each other.
            embedding_name = "wowtools_scoreboard" if GAME == "retail" else f"wowtools_{GAME}_scoreboard"
            await assistant.add_embedding(guild, embedding_name, formatted_rankings, True)
        except Exception as e:
            log.error(f"Error adding scoreboard to Assistant: {e}", exc_info=True)

    async def get_season_title_cutoff(self, region: str) -> float:
        # Current main season from raider.io static data (was a hard-coded slug).
        current_season = await self.get_current_mplus_season(region)
        if not current_season:
            return 0
        try:
            data = await self.raiderio_api.get_mythic_plus_season_cutoffs(region, current_season)
        except Exception as e:
            log.debug("Fetching season cutoffs failed: %s", e)
            return 0
        cutoffs = (data or {}).get("cutoffs") or {}
        try:
            return cutoffs["p999"]["all"]["quantileMinValue"]
        except (KeyError, TypeError):
            return 0

    @update_dungeon_scoreboard.error
    async def update_dungeon_scoreboard_error(self, error):
        # Thanks Flame!
        log.error(f"Unhandled error in update_dungeon_scoreboard task: {error}", exc_info=True)

    async def _get_dungeon_scores(
        self,
        guild_name: str,
        max_chars: int,
        realm: str,
        region: str,
        sb_blacklist: List[str],
        image: bool,
    ):
        roster = await self.raiderio_api.get_guild_roster(region, realm, guild_name)
        if "error" in roster.keys():
            raise ValueError(f"{roster['message']}.")

        lb = {}
        # TODO: Surely there's a better way to do literally everything below
        for char in roster["guildRoster"]["roster"]:
            char_name: str = char["character"]["name"]
            if any(char.isdigit() for char in char_name):
                continue

            score = char["keystoneScores"]["allScore"]

            if score > 250 and char_name.lower() not in sb_blacklist:
                if image:
                    class_color: str = ClassColor.get_class_color(
                        char["character"]["class"]["name"]
                    )
                    char_img: str = (
                        "https://render.worldofwarcraft.com/{region}/character/{}".format(
                            char["character"]["thumbnail"], region=region
                        )
                    )
                    ilvl = str(char["character"]["items"]["item_level_equipped"])
                    score_color: str = char["keystoneScores"]["allScoreColor"]
                    lb[char_name] = (score, score_color, char_img, class_color, ilvl)
                else:
                    lb[char_name] = score

        lb = dict(sorted(lb.items(), key=lambda i: i[1], reverse=True))

        chars = list(lb.keys())[:max_chars]
        scores = list(lb.values())[:max_chars]

        tabulate_list = []
        for index, char_info in enumerate(zip(chars, scores)):
            char_name = char_info[0]
            if image:
                char_score = char_info[1][0]
                char_score_color = char_info[1][1]
                char_img = char_info[1][2]
                class_color = char_info[1][3]
                ilvl = char_info[1][4]
                tabulate_list.append(
                    [
                        f"{index + 1}.",
                        char_name,
                        str(int(char_score)),
                        char_score_color,
                        char_img,
                        class_color,
                        ilvl,
                    ]
                )
            else:
                char_score = char_info[1]
                tabulate_list.append(
                    [
                        f"{index + 1}.",
                        char_name,
                        humanize_number(int(char_score)),
                    ]
                )

        return tabulate_list

    async def _generate_dungeon_scoreboard(self, ctx: commands.Context, image: bool = False):
        max_chars = 20
        headers = ["#", _("Name"), _("Score")]
        guild_name, realm, region, sb_blacklist = await self._get_guild_config(ctx)
        if not region:
            raise ValueError(
                _(
                    "\nA server admin needs to set a region with `{prefix}wowset region` first."
                ).format(prefix="" if ctx.interaction else ctx.clean_prefix)
            )
        if not realm:
            raise ValueError(
                _(
                    "\nA server admin needs to set a realm with `{prefix}wowset realm` first."
                ).format(prefix="" if ctx.interaction else ctx.clean_prefix)
            )
        if not guild_name:
            raise ValueError(
                _(
                    "\nA server admin needs to set a guild name with `{prefix}wowset guild` first."
                ).format(prefix="" if ctx.interaction else ctx.clean_prefix)
            )

        embed = discord.Embed(
            title=_("Mythic+ Guild Scoreboard"),
            color=await ctx.embed_color(),
        )
        # ctx.guild.icon can be None (no icon set).
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)

        tabulate_list = await self._get_dungeon_scores(
            guild_name, max_chars, realm, region, sb_blacklist, image
        )

        if image:
            img_file = await self._generate_scoreboard_image(
                tabulate_list, dev_guild=ctx.guild.id in DEV_GUILDS
            )
            embed.set_image(url=f"attachment://{img_file.filename}")
            return embed, img_file
        else:
            embed.description = box(
                tabulate(
                    tabulate_list,
                    headers=headers,
                    tablefmt="plain",
                    disable_numparse=True,
                ),
                lang="md",
            )
            return embed

    async def _generate_scoreboard_image(self, tabulate_list: list, dev_guild: bool = False):
        img_path = str(
            bundled_data_path(self) / "scoreboard-jrk.png"
            if dev_guild
            else bundled_data_path(self) / "scoreboard-df-s1.png"
        )
        img = Image.open(img_path)
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype(str(bundled_data_path(self) / "Roboto-Bold.ttf"), 28)

        x = 150
        y = 100 if dev_guild else 25

        for character in tabulate_list[:10]:
            char_name = character[1]
            score = character[2]
            ilvl = character[6]

            score_color = ImageColor.getcolor(character[3], "RGB")
            class_color = ImageColor.getcolor(character[5], "RGB")
            ilvl_color = self._get_ilvl_color(int(ilvl))

            async with self.session.request("GET", character[4]) as resp:
                image = await resp.content.read()
                image = Image.open(io.BytesIO(image))
                image = image.resize((65, 65))
                img.paste(image, (x - 79, y - 15))

            draw.text((x, y), char_name, class_color, font=font)
            if int(ilvl) >= 679:  # This is whatever the color for the highest ilvl is
                glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
                ImageDraw.Draw(glow).text((x + 225, y), ilvl, ilvl_color, font=font)
                blurred_glow = glow.filter(ImageFilter.GaussianBlur(5))
                ImageDraw.Draw(blurred_glow).text((x + 225, y), ilvl, ilvl_color, font=font)
                img = Image.alpha_composite(img, blurred_glow)
                # have to reassing draw
                draw = ImageDraw.Draw(img)
            else:
                draw.text((x + 225, y), ilvl, ilvl_color, font=font)
            draw.text((x + 300, y), score, score_color, font=font)
            y += 75

        img_obj = io.BytesIO()
        img.save(img_obj, format="PNG")
        img_obj.seek(0)

        return discord.File(fp=img_obj, filename="scoreboard.png")

    @staticmethod
    def _get_ilvl_color(ilvl: int) -> str:
        if ilvl >= 717:
            return "#f16960"
        elif ilvl >= 714:
            return "#FF69B4"
        elif ilvl >= 709:
            return "#FFA500"
        elif ilvl >= 705:
            return "#b040c2"
        elif ilvl >= 698:
            return "#445bc2"
        elif ilvl >= 692:
            return "#00ff1a"
        else:
            return "#FFFFFF"

    @staticmethod
    async def _delete_scoreboard(ctx: commands.Context, sb_channel_id: int, sb_msg_id: int):
        try:
            sb_channel: discord.TextChannel = ctx.guild.get_channel(sb_channel_id)
            sb_msg: discord.Message = await sb_channel.fetch_message(sb_msg_id)
        except discord.NotFound:
            sb_msg = None
            log.info(f"Scoreboard message in {ctx.guild} ({ctx.guild.id}) not found.")
        if sb_msg:
            await sb_msg.delete()

    async def _generate_pvp_scoreboard(self, ctx: commands.Context) -> Optional[discord.Embed]:
        max_chars = 10
        headers = ["#", _("Name"), _("Rating")]
        guild_name, realm, region, sb_blacklist = await self._get_guild_config(ctx)
        if not region:
            await ctx.send(
                _(
                    "\nA server admin needs to set a region with `{prefix}wowset region` first."
                ).format(prefix="" if ctx.interaction else ctx.clean_prefix)
            )
            return None
        if not realm:
            await ctx.send(
                _(
                    "\nA server admin needs to set a realm with `{prefix}wowset realm` first."
                ).format(prefix="" if ctx.interaction else ctx.clean_prefix)
            )
            return None
        if not guild_name:
            await ctx.send(
                _(
                    "\nA server admin needs to set a guild name with `{prefix}wowset guild` first."
                ).format(prefix="" if ctx.interaction else ctx.clean_prefix)
            )
            return None

        msg = await ctx.send(
            _("Fetching scoreboard...\n" "This can take up to 30 minutes for very large guilds.")
        )

        embed_pvp = discord.Embed(
            title=_("Guild PvP Leaderboard"),
            color=await ctx.embed_color(),
        )

        tabulate_lists = await self._get_pvp_scores(
            ctx, guild_name, max_chars, realm, region, sb_blacklist
        )
        if not tabulate_lists:
            await msg.delete()
            return None

        embed_pvp.add_field(
            name=_("RBG Leaderboard"),
            value=box(
                tabulate(
                    tabulate_lists[0],
                    headers=headers,
                    tablefmt="plain",
                    disable_numparse=True,
                ),
                lang="md",
            ),
            inline=False,
        )
        embed_pvp.add_field(
            name=_("2v2 Arena Leaderboard"),
            value=box(
                tabulate(
                    tabulate_lists[1],
                    headers=headers,
                    tablefmt="plain",
                    disable_numparse=True,
                ),
                lang="md",
            ),
            inline=False,
        )
        embed_pvp.add_field(
            name=_("3v3 Arena Leaderboard"),
            value=box(
                tabulate(
                    tabulate_lists[2],
                    headers=headers,
                    tablefmt="plain",
                    disable_numparse=True,
                ),
                lang="md",
            ),
            inline=False,
        )

        await msg.delete()
        return embed_pvp

    async def _get_pvp_scores(
        self,
        ctx,
        guild_name: str,
        max_chars: int,
        realm: str,
        region: str,
        sb_blacklist: List[str],
    ) -> list:
        guild_name = guild_name.replace(" ", "-").lower()

        max_level = 80
        # Plain REST calls in this cog's namespace (aiowowapi 2.1.5 has no Classic.Profile).
        current_season = await self.get_current_pvp_season_id(region)
        try:
            guild_roster = await fetch_guild_roster(self, region, realm, guild_name)
        except BlizzardAPIError:
            await ctx.send(_("Guild not found."))
            return []

        roster = {"rbg": {}, "2v2": {}, "3v3": {}}

        for member in guild_roster.get("members") or []:
            character = (member or {}).get("character") or {}
            character_name = (character.get("name") or "").lower()
            if not character_name or character_name in sb_blacklist:
                continue
            if (character.get("level") or 0) < max_level:
                continue

            log.debug(f"Getting PvP data for {character_name}")
            statistics = {}
            for bracket in ("rbg", "2v2", "3v3"):
                try:
                    await self.limiter.acquire()
                    statistics[bracket] = await fetch_character(
                        self, region, realm, character_name, f"pvp-bracket/{bracket}"
                    )
                except BlizzardAPIError:
                    statistics[bracket] = {}

            for bracket, stats in statistics.items():
                if "rating" in stats and (stats.get("season") or {}).get("id") == current_season:
                    roster[bracket][character_name] = stats["rating"]
                    log.debug(f"{character_name} has {bracket} rating {stats['rating']}")

        roster["rbg"] = dict(sorted(roster["rbg"].items(), key=lambda i: i[1], reverse=True))
        roster["2v2"] = dict(sorted(roster["2v2"].items(), key=lambda i: i[1], reverse=True))
        roster["3v3"] = dict(sorted(roster["3v3"].items(), key=lambda i: i[1], reverse=True))

        return await self._make_tabulate_lists(max_chars, roster)

    @staticmethod
    async def _make_tabulate_lists(max_chars, roster):
        characters_rbg = list(roster["rbg"].keys())[:max_chars]
        characters_2v2 = list(roster["2v2"].keys())[:max_chars]
        characters_3v3 = list(roster["3v3"].keys())[:max_chars]
        ratings_rbg = list(roster["rbg"].values())[:max_chars]
        ratings_2v2 = list(roster["2v2"].values())[:max_chars]
        ratings_3v3 = list(roster["3v3"].values())[:max_chars]

        tabulate_lists = [[], [], []]  # [RBG, 2v2, 3v3]
        await Scoreboard._make_rbg_tabulate_list(characters_rbg, ratings_rbg, tabulate_lists)
        await Scoreboard._make_2v2_tabulate_list(characters_2v2, ratings_2v2, tabulate_lists)
        await Scoreboard._make_3v3_tabulate_list(characters_3v3, ratings_3v3, tabulate_lists)
        return tabulate_lists

    @staticmethod
    async def _make_3v3_tabulate_list(characters_3v3, ratings_3v3, tabulate_lists):
        for index, char_info in enumerate(zip(characters_3v3, ratings_3v3)):
            char_name: str = char_info[0]
            char_rating: int = char_info[1]
            tabulate_lists[2].append(
                [
                    f"{index + 1}.",
                    char_name.capitalize(),
                    humanize_number(char_rating),
                ]
            )

    @staticmethod
    async def _make_2v2_tabulate_list(characters_2v2, ratings_2v2, tabulate_lists):
        for index, char_info in enumerate(zip(characters_2v2, ratings_2v2)):
            char_name: str = char_info[0]
            char_rating: int = char_info[1]
            tabulate_lists[1].append(
                [
                    f"{index + 1}.",
                    char_name.capitalize(),
                    humanize_number(char_rating),
                ]
            )

    @staticmethod
    async def _make_rbg_tabulate_list(characters_rbg, ratings_rbg, tabulate_lists):
        for index, char_info in enumerate(zip(characters_rbg, ratings_rbg)):
            char_name: str = char_info[0]
            char_rating: int = char_info[1]
            tabulate_lists[0].append(
                [
                    f"{index + 1}.",
                    char_name.capitalize(),
                    humanize_number(char_rating),
                ]
            )

    async def _get_guild_config(self, ctx):
        region: str = await self.config.guild(ctx.guild).region()
        realm: str = await self.config.guild(ctx.guild).realm()
        guild_name: str = await self.config.guild(ctx.guild).real_guild_name()
        sb_blacklist: List[str] = await self.config.guild(ctx.guild).scoreboard_blacklist()
        return guild_name, realm, region, sb_blacklist


class ClassColor(Enum):
    DEATH_KNIGHT = "#C41F3B"
    DEMON_HUNTER = "#A330C9"
    DRUID = "#FF7D0A"
    HUNTER = "#ABD473"
    MAGE = "#69CCF0"
    MONK = "#00FF96"
    PALADIN = "#F58CBA"
    PRIEST = "#FFFFFF"
    ROGUE = "#FFF569"
    SHAMAN = "#0070DE"
    WARLOCK = "#9482C9"
    WARRIOR = "#C79C6E"
    EVOKER = "#1F594D"

    @staticmethod
    def get_class_color(player_class: str) -> str:
        try:
            return ClassColor[player_class.upper().replace(" ", "_")].value
        except KeyError:
            return "#FFFFFF"

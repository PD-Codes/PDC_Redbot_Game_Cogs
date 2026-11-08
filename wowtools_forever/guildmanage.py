import asyncio
import logging
import unicodedata
from typing import Optional

import dictdiffer
import discord
from discord import app_commands
from discord.ext import tasks
from rapidfuzz import fuzz, process
from redbot.core import commands
from redbot.core.i18n import Translator, set_contextual_locales_from_guild
from redbot.core.utils.chat_formatting import humanize_list
from tabulate import tabulate

from .exceptions import InvalidBlizzardAPI
from .forever_api import fetch_guild, guild_slug, member_display_name
from .pdc_dashboard import tr_lang

_ = Translator("WoWToolsForever", __file__)
log = logging.getLogger("red.karlo-cogs.wowtools_forever")

# Above this many joins/leaves in one roster diff, no per-member embeds (and no
# fuzzy Discord matching) are built; a single summary embed is sent instead.
_MAX_DETAILED_EVENTS = 20


class GuildManage:
    @commands.hybrid_group(
        name="wowtf-gmset",
        description="Configure guild management.",
        extras={"i18n_desc": {
            "de-DE": "Gildenverwaltung konfigurieren.",
            "en-US": "Configure guild management.",
        }},
    )
    @commands.admin()
    @commands.guild_only()
    async def gmset(self, ctx: commands.Context):
        """Configure guild management."""
        pass

    @gmset.command(
        name="rankstring",
        description="Bind a rank to a string.",
        extras={"i18n_desc": {
            "de-DE": "Einen Rang an einen Text binden.",
            "en-US": "Bind a rank to a string.",
        }},
    )
    @commands.admin()
    @app_commands.describe(rank="Rank number (1-10)", rank_string="The label to bind to the rank")
    async def gmset_rankstring(self, ctx: commands.Context, rank: int, *, rank_string: str):
        """Bind a rank to a string."""
        if rank not in range(1, 11):
            await ctx.send(_("Rank must be between 1 and 10."))
            return
        await self.config.guild(ctx.guild).guild_rankstrings.set_raw(rank, value=rank_string)
        await ctx.send(
            _("**{rank_string}** bound to **Rank {rank}**.").format(
                rank_string=rank_string, rank=rank
            )
        )

    @gmset.command(
        name="rankrole",
        hidden=True,
        description="Bind a rank to a role.",
        extras={"i18n_desc": {
            "de-DE": "Einen Rang an eine Rolle binden.",
            "en-US": "Bind a rank to a role.",
        }},
    )
    @commands.admin()
    @app_commands.describe(rank="Rank number (1-10)", role="The role to bind to the rank")
    async def gmset_rankrole(self, ctx: commands.Context, rank: int, role: discord.Role):
        """Bind a rank to a role."""
        if rank not in range(1, 11):
            await ctx.send(_("Rank must be between 1 and 10."))
            return
        await self.config.guild(ctx.guild).guild_rankroles.set_raw(rank, value=role.id)
        await ctx.send(_("**{role}** bound to **Rank {rank}**.").format(role=role.name, rank=rank))

    @gmset.command(
        name="view",
        description="View guild rank settings.",
        extras={"i18n_desc": {
            "de-DE": "Gildenrang-Einstellungen anzeigen.",
            "en-US": "View guild rank settings.",
        }},
    )
    @commands.admin()
    async def gmset_view(self, ctx: commands.Context):
        """View guild rank settings."""
        guild: discord.Guild = ctx.guild
        rank_strings = await self.config.guild(guild).guild_rankstrings.get_raw()
        rank_roles = await self.config.guild(guild).guild_rankroles.get_raw()

        table_data = []
        for rank in range(1, 11):
            rank_string = rank_strings.get(str(rank), "")
            role_id = rank_roles.get(str(rank), None)
            role = guild.get_role(role_id) if role_id else None

            table_data.append([rank, rank_string, f"@{role.name}" if role else ""])

        headers = [_("Rank"), _("Rank String"), _("Rank Role")]
        table = tabulate(table_data, headers, tablefmt="plain")

        if ctx.channel.permissions_for(ctx.guild.me).embed_links:
            embed = discord.Embed(
                title=_("Rank Settings"),
                description=f"```{table}```",
                color=await ctx.embed_color(),
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"Rank Settings:\n```{table}```")

    @gmset.command(
        description="Set the in-game guild name for guild management.",
        extras={"i18n_desc": {
            "de-DE": "Den ingame Gildennamen für die Gildenverwaltung festlegen.",
            "en-US": "Set the in-game guild name for guild management.",
        }},
    )
    @commands.admin()
    @app_commands.describe(guild_name="The in-game guild name (empty = clear)")
    async def guild_name(self, ctx: commands.Context, *, guild_name: Optional[str] = None):
        """Set the guild name to be used in the guild management commands."""
        try:
            async with ctx.typing():
                if not (guild_name or "").strip():
                    await self.set_wow_guild_name(ctx.guild, None)
                    await ctx.send(_("Guild name cleared."))
                    return
                await self.set_wow_guild_name(ctx.guild, guild_name)
            await ctx.send(_("Guild name set."))
        except Exception as e:
            await ctx.send(_("Command failed successfully. {e}").format(e=e))

    async def get_wow_guild_name(self, guild: discord.Guild) -> Optional[str]:
        """The configured in-game guild name.

        ``gmanage_guild`` is the single source of truth. ``real_guild_name`` is only
        read as a fallback for data written by older versions of this cog.
        """
        conf = self.config.guild(guild)
        name = await conf.gmanage_guild()
        if not name:
            name = await conf.real_guild_name()
        return name or None

    async def set_wow_guild_name(self, guild: discord.Guild, guild_name: Optional[str]) -> None:
        """Store (or clear with None/"") the in-game guild name in ``gmanage_guild``."""
        conf = self.config.guild(guild)
        guild_name = (guild_name or "").strip()
        if not guild_name:
            await conf.gmanage_guild.clear()
            # Also drop the legacy key, otherwise the read fallback would revive it.
            await conf.real_guild_name.clear()
            return
        # WoW Forever guilds are realmless; fetch_guild() builds the slug from this name.
        await conf.gmanage_guild.set(guild_name)

    async def save_roster_baseline(self, guild: discord.Guild, roster: dict) -> None:
        """Store ``roster`` as the comparison base together with the guild it belongs to."""
        await self.config.guild(guild).guild_roster.set(roster)
        wow_guild = await self.get_wow_guild_name(guild)
        await self.config.guild(guild).guild_roster_guild.set(guild_slug(wow_guild) if wow_guild else None)

    async def get_guild_roster(self, guild: discord.Guild) -> dict[str, int]:
        """
        Get the guild roster from Blizzard's API (WoW Forever, realmless guild).

        :param guild:
        :return: dict mapping "Name Surname:slug" to the 1-based guild rank
        """
        wow_guild_name: Optional[str] = await self.get_wow_guild_name(guild)
        region: str = await self.config.guild(guild).region()
        if not wow_guild_name or not region:
            # Guild name or region missing -> callers treat AttributeError as "not configured".
            raise AttributeError("WoW guild name or region not configured")

        guild_roster = await fetch_guild(self, region, wow_guild_name, "roster")

        roster: dict[str, int] = {}
        for member in guild_roster.get("members") or []:
            character = member.get("character") or {}
            display = member_display_name(character).replace(":", " ")
            slug = character.get("slug") or display.lower().replace(" ", "-")
            try:
                rank = int(member.get("rank", 0)) + 1
            except (TypeError, ValueError):
                rank = 1
            roster[f"{display}:{slug}"] = rank
        return roster

    @gmset.command(
        description="Set the channel for guild join/leave/promotion logs.",
        extras={"i18n_desc": {
            "de-DE": "Den Kanal für Gilden-Logs (Beitritt/Austritt/Beförderung) festlegen.",
            "en-US": "Set the channel for guild join/leave/promotion logs.",
        }},
    )
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.describe(channel="The channel where guild logs will be sent")
    async def guildlog(self, ctx: commands.Context, channel: discord.TextChannel | discord.Thread):
        """Set the channel for guild logs.

        This channel will be used to send messages when a member joins, leaves or is promoted/demoted within the in-game guild.
        """
        try:
            guild_roster = await self.get_guild_roster(ctx.guild)
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
        except AttributeError:
            await ctx.send(
                _("Please use `{prefix}wowtf-gmset` and set the name of your guild (and `{prefix}wowtf-serverset region`).").format(
                    prefix=ctx.prefix
                )
            )
            return
        except RuntimeError as e:
            # ForeverAPIError (e.g. 404 guild not found) or auth problems
            await ctx.send(_("Command failed successfully. {e}").format(e=e))
            return
        await self.config.guild(ctx.guild).guild_log_channel.set(channel.id)
        await self.save_roster_baseline(ctx.guild, guild_roster)
        await ctx.send(_("Guild log channel set to {channel}.").format(channel=channel.mention))

    @gmset.command(
        description="Set the channel for welcoming new in-game guild members.",
        extras={"i18n_desc": {
            "de-DE": "Den Kanal zum Begrüßen neuer ingame Gildenmitglieder festlegen.",
            "en-US": "Set the channel for welcoming new in-game guild members.",
        }},
    )
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.describe(channel="The channel where welcome messages will be sent")
    async def guildlog_welcome(
        self, ctx: commands.Context, channel: discord.TextChannel | discord.Thread
    ):
        """Set the guild log welcome channel.

        When a user joins this server, a message will be sent to the provided channel with their in-game name and rank if the bot is able to find them.
        """
        await self.config.guild(ctx.guild).guild_log_welcome_channel.set(channel.id)
        try:
            guild_roster = await self.get_guild_roster(ctx.guild)
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
        except AttributeError:
            await ctx.send(
                _("Please use `{prefix}wowtf-gmset` and set the name of your guild (and `{prefix}wowtf-serverset region`).").format(
                    prefix=ctx.prefix
                )
            )
            return
        except RuntimeError as e:
            # ForeverAPIError (e.g. 404 guild not found) or auth problems
            await ctx.send(_("Command failed successfully. {e}").format(e=e))
            return
        await self.save_roster_baseline(ctx.guild, guild_roster)
        await ctx.send(_("Guild log channel set to {channel}.").format(channel=channel.mention))

    @tasks.loop(minutes=5)
    async def guild_log(self):
        for guild in self.bot.guilds:
            # Any per-guild failure must not kill the whole task loop.
            try:
                if await self.bot.cog_disabled_in_guild(self, guild):
                    continue
                await set_contextual_locales_from_guild(self.bot, guild)

                guild_log_channel: int = await self.config.guild(guild).guild_log_channel()
                if guild_log_channel is None:
                    continue
                guild_log_channel: discord.TextChannel | discord.Thread = (
                    guild.get_channel_or_thread(guild_log_channel)
                )
                if guild_log_channel is None:
                    continue

                # Skip guilds without a configured WoW guild name/region,
                # otherwise get_guild_roster() would crash on None.lower().
                wow_guild = await self.get_wow_guild_name(guild)
                if not wow_guild:
                    continue
                if not await self.config.guild(guild).region():
                    continue

                log.debug("Comparing guild rosters.")
                try:
                    current_roster = await self.get_guild_roster(guild)
                except InvalidBlizzardAPI:
                    log.warning(
                        "The Blizzard API is not properly set up.\n"
                        "Create a client on https://develop.battle.net/ and then type in "
                        "`{prefix}set api blizzard client_id,whoops client_secret,whoops` "
                        "filling in `whoops` with your client's ID and secret."
                    )
                    continue
                except RuntimeError:
                    # idk, try again later
                    continue
                previous_roster: dict[str, int] = await self.config.guild(guild).guild_roster()
                previous_guild = await self.config.guild(guild).guild_roster_guild()

                # First run, setup via dashboard (no baseline yet) or a different WoW
                # guild than the stored roster belongs to: only store the baseline,
                # otherwise every member would be reported as "joined".
                if not previous_roster or previous_guild != guild_slug(wow_guild):
                    await self.save_roster_baseline(guild, current_roster)
                    continue

                # Roster keys are `Name Surname:slug`; compare on the display part only.
                prev_for_diff: dict[str, int] = {}
                for name, rank in previous_roster.items():
                    # Converting `Name Surname:slug` to just `Name Surname`
                    prev_for_diff[name.split(":")[0]] = rank
                current_for_diff: dict[str, int] = {}
                for name, rank in current_roster.items():
                    current_for_diff[name.split(":")[0]] = rank

                difference = list(dictdiffer.diff(prev_for_diff, current_for_diff))
                if not difference:
                    log.debug("No difference in guild roster.")
                    continue

                embeds = await self.get_event_embeds(difference, guild)
                await self.save_roster_baseline(guild, current_roster)

                for i in range(0, len(embeds), 10):
                    await guild_log_channel.send(embeds=embeds[i : i + 10], silent=True)
            except Exception as e:
                log.exception("Guild log update failed for guild %s: %s", guild.id, e)
                continue

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if guild is None:
            return
        if await self.bot.cog_disabled_in_guild(self, guild):
            return
        if member.bot:
            return

        # Check the welcome channel first so no API call is made when it is not set.
        guild_log_channel_id: int = await self.config.guild(guild).guild_log_welcome_channel()
        if not guild_log_channel_id:
            return
        guild_log_channel = guild.get_channel_or_thread(guild_log_channel_id)
        if not guild_log_channel:
            return

        try:
            ingame_members, rank = await self.guess_ingame_member(guild, member.display_name)
        except InvalidBlizzardAPI:
            log.warning(
                "The Blizzard API is not properly set up.\n"
                "Create a client on https://develop.battle.net/ and then type in "
                "`{prefix}set api blizzard client_id,whoops client_secret,whoops` "
                "filling in `whoops` with your client's ID and secret."
            )
            return
        except (AttributeError, ValueError, RuntimeError):
            return
        if not ingame_members:
            return

        desc = f"Discord: {member.mention}\n"
        desc += f"In-game: {humanize_list([name.split(':')[0] for name in ingame_members], style='or')}?\nRank: {rank}?\n"

        embed = discord.Embed(
            description=desc,
            color=discord.Colour.green(),
        )

        if guild_log_channel.permissions_for(guild.me).embed_links:
            await guild_log_channel.send(
                embed=embed,
                silent=True,
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=False),
            )
        else:
            await guild_log_channel.send(
                content=desc,
                silent=True,
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=False),
            )

    async def get_event_embeds(self, difference, guild):
        embeds = []
        added = [m for d in difference if d[0] == "add" for m in d[2]]
        removed = [m for d in difference if d[0] == "remove" for m in d[2]]
        for diff in difference:
            if diff[0] == "add":
                if len(added) <= _MAX_DETAILED_EVENTS:
                    await self.add_new_members(diff, embeds, guild)
            elif diff[0] == "change":
                await self.add_changed_members(diff, embeds, guild)
            elif diff[0] == "remove":
                if len(removed) <= _MAX_DETAILED_EVENTS:
                    await self.add_removed_members(diff, embeds, guild)
        # Many changes at once: one summary per kind, no fuzzy matching per member.
        lang = await self.config.guild(guild).language()
        if len(added) > _MAX_DETAILED_EVENTS:
            embeds.insert(0, self.make_summary_embed(
                tr_lang(
                    lang,
                    f"{len(added)} Mitglieder sind der Gilde beigetreten",
                    f"{len(added)} members joined the guild",
                ),
                [m[0] for m in added],
                discord.Colour.green(),
            ))
        if len(removed) > _MAX_DETAILED_EVENTS:
            embeds.insert(0, self.make_summary_embed(
                tr_lang(
                    lang,
                    f"{len(removed)} Mitglieder haben die Gilde verlassen",
                    f"{len(removed)} members left the guild",
                ),
                [m[0] for m in removed],
                discord.Colour.red(),
            ))
        return embeds

    @staticmethod
    def make_summary_embed(title: str, names: list, colour: discord.Colour) -> discord.Embed:
        text = ""
        for idx, name in enumerate(sorted(names, key=str.lower)):
            entry = f"{name}, " if idx < len(names) - 1 else name
            if len(text) + len(entry) > 3900:
                text = text.rstrip(", ") + f", … (+{len(names) - idx})"
                break
            text += entry
        return discord.Embed(title=title, description=text, color=colour)

    async def add_new_members(self, diff, embeds: list, guild: discord.Guild) -> None:
        for member in diff[2]:
            member_name = member[0]
            member_rank_new = await self.get_rank_string(guild, member[1])

            embed = discord.Embed(
                title=_("**{member}** joined the guild as **{rank}**").format(
                    member=member_name, rank=member_rank_new
                ),
                description=await self.make_description(guild, member_name),
                color=discord.Colour.green(),
            )
            embeds.append(embed)

    async def add_changed_members(self, diff, embeds: list, guild: discord.Guild) -> None:
        member_name = diff[1]
        member_rank_old = await self.get_rank_string(guild, diff[2][0])
        member_rank_new = await self.get_rank_string(guild, diff[2][1])

        embed = discord.Embed(
            title=_("**{member}** was {changed} from **{old_rank}** to **{new_rank}**").format(
                member=member_name,
                old_rank=member_rank_old,
                new_rank=member_rank_new,
                changed=_("promoted") if diff[2][0] > diff[2][1] else _("demoted"),
            ),
            description=await self.make_description(guild, member_name),
            color=discord.Colour.blurple(),
        )
        embeds.append(embed)

    async def add_removed_members(self, diff, embeds: list, guild: discord.Guild) -> None:
        for member in diff[2]:
            member_name = member[0]
            member_rank_new = await self.get_rank_string(guild, member[1])

            embed = discord.Embed(
                title=_("**{member} ({rank})** left the guild").format(
                    member=member_name, rank=member_rank_new
                ),
                description=await self.make_description(guild, member_name),
                color=discord.Colour.red(),
            )
            embeds.append(embed)

    async def make_description(
        self,
        guild: discord.Guild,
        member_name: str,
    ) -> str:
        return humanize_list(await self.guess_member(guild, member_name), style="or")

    async def guess_member(self, guild: discord.Guild, member_name: str) -> list[str]:
        """
        Guesses the Discord member based on their name using fuzzy string matching.

        :param guild: The Discord guild to search for the member in.
        :param member_name: The name of the character to search for.
        :return: A tuple containing mentions of the Discord members that match the character name.
        """
        # Snapshot the member data in the event loop, run the CPU-heavy fuzzy
        # matching in a worker thread so large servers don't block the bot.
        members = list(guild.members)
        name_sets = [
            [n for n in {m.display_name, m.name, m.nick} if n is not None] for m in members
        ]
        indices = await asyncio.to_thread(self._fuzzy_member_indices, member_name, name_sets)
        return [members[i].mention for i in indices]

    def _fuzzy_member_indices(self, member_name: str, name_sets: list) -> list:
        """Pure function (thread-safe): indices of the best matching members."""
        choices: list[str] = []
        for names in name_sets:
            extract = process.extractOne(
                member_name,
                names,
                scorer=fuzz.WRatio,
                processor=self.custom_processor,
            )
            choices.append(extract[0] if extract else "")

        extract = process.extract(
            member_name,
            choices,
            scorer=fuzz.WRatio,
            limit=10,
            score_cutoff=80,
            processor=self.custom_processor,
        )
        return [member[2] for member in extract]

    @guild_log.error
    async def guild_log_error(self, error):
        log.error(f"Unhandled exception in guild_log task: {error}", exc_info=True)

    async def get_rank_string(self, guild: discord.Guild, rank: int) -> str:
        rank_strings: dict = await self.config.guild(guild).guild_rankstrings()
        return rank_strings.get(str(rank), f"Rank {rank}")

    @commands.hybrid_group(
        name="wowtf-gmanage",
        description="Guild management commands.",
        extras={"i18n_desc": {
            "de-DE": "Befehle zur Gildenverwaltung.",
            "en-US": "Guild management commands.",
        }},
    )
    @commands.guild_only()
    async def gmanage(self, ctx: commands.Context):
        """Guild management commands."""
        pass

    @gmanage.command(
        name="find",
        description="Find a member in the guild.",
        extras={"i18n_desc": {
            "de-DE": "Ein Mitglied in der Gilde suchen.",
            "en-US": "Find a member in the guild.",
        }},
    )
    @commands.guild_only()
    @app_commands.describe(member_name="The character or member name to search for")
    async def gmanage_find(self, ctx: commands.Context, member_name: str):
        """Find a member in the guild."""
        msg = ""
        discord_members = await self.guess_member(ctx.guild, member_name)
        if discord_members:
            msg += f"Discord: {humanize_list(discord_members, style='or')}\n"

        member_name = member_name.title()
        try:
            ingame_members, rank = await self.guess_ingame_member(ctx.guild, member_name)
        except ValueError:
            ingame_members, rank = (None, None)
        except AttributeError:
            await ctx.send(
                _("Please use `{prefix}wowtf-gmset` and set the name of your guild (and `{prefix}wowtf-serverset region`).").format(
                    prefix=ctx.prefix
                )
            )
            return
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
        except RuntimeError as e:
            await ctx.send(_("Command failed successfully. {e}").format(e=e))
            return
        if ingame_members:
            msg += f"In-game: {humanize_list([member.split(':')[0] for member in ingame_members], style='or')}\nRank: {rank}\n"

        if ctx.channel.permissions_for(ctx.guild.me).embed_links:
            embed = discord.Embed(
                description=msg or _("Nothing found."),
                color=await ctx.embed_color(),
            )
            await ctx.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=False),
            )
        else:
            await ctx.send(content=msg or _("Nothing found."))

    async def guess_ingame_member(
        self, guild: discord.Guild, member_name: str
    ) -> tuple[list[str], str]:
        roster = await self.get_guild_roster(guild)
        name_mapping: dict[str, str] = {name.split(":")[0]: name for name in roster.keys()}
        extract = process.extract(
            member_name,
            set(name_mapping.keys()),
            scorer=fuzz.WRatio,
            limit=10,
            score_cutoff=80,
            processor=self.custom_processor,
        )
        extract.sort(key=lambda member: roster[name_mapping[member[0]]])
        ranks: list[int] = [roster[name_mapping[member[0]]] for member in extract]
        ingame_rank = await self.get_rank_string(guild, min(ranks))
        return [name_mapping[member[0]] for member in extract], ingame_rank

    def custom_processor(self, string: str) -> str:
        return "".join(
            string
            for string in unicodedata.normalize("NFD", string)
            if unicodedata.category(string) != "Mn"
        )

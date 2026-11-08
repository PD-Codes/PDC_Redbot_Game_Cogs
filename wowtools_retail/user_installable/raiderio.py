from typing import List

import discord
from discord import app_commands
from discord.app_commands import AppCommandContext, AppInstallationType
from redbot.core.commands import Context
from redbot.core.i18n import Translator

from ..raiderio import ProfileMenu, Raiderio
from ..utils import get_realms

_ = Translator("WoWTools", __file__)


class UserInstallableRaiderio:
    user_install_raiderio = app_commands.Group(
        name="wowt-raiderio",
        description=_("raider.io commands"),
        allowed_installs=AppInstallationType(guild=True, user=True),
        allowed_contexts=AppCommandContext(guild=True, dm_channel=True, private_channel=True),
    )

    @user_install_raiderio.command(name="profile")
    @app_commands.describe(
        character="Name of the character to look up",
        realm="The character's realm",
    )
    async def user_install_raiderio_profile(
        self, interaction: discord.Interaction, character: str, realm: str
    ) -> None:
        """Display the raider.io profile of a character.

        **Example:**
        [p]raiderio profile Karlo Ragnaros
        """
        # Validate before deferring: the realm option must be "Realm:Region"
        # (the autocomplete fills it in that format).
        realm, sep, region = (realm or "").rpartition(":")
        region = region.strip().lower()
        if not sep or not realm.strip() or region not in ("eu", "us", "kr", "tw"):
            await interaction.response.send_message(
                _("Please pick a realm from the list or use the format `Realm:Region`, e.g. `Ragnaros:EU`."),
                ephemeral=True,
            )
            return
        realm = realm.strip().lower().replace(" ", "-")
        await interaction.response.defer()
        profile_data = await self.rio_character_profile(  # type: ignore
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
            await interaction.followup.send(_("Character not found."))
            return
        # All of the following fields can be missing or null in the API
        # response (same handling as the prefix command in ../raiderio.py).
        char_race = profile_data.get("race", "N/A")
        char_spec = profile_data.get("active_spec_name", "N/A")
        char_class = profile_data.get("class", "N/A")
        char_guild = (profile_data.get("guild") or {}).get("name")
        char_image = profile_data.get("thumbnail_url")
        seasons = profile_data.get("mythic_plus_scores_by_season") or []
        char_score = ((seasons[0].get("segments") or {}).get("all") if seasons else None) or {}
        char_score_color = int("0x" + str(char_score.get("color", "#ffffff"))[1:], 0)
        char_raid = Raiderio.raid_summary(
            profile_data.get("raid_progression"), await self.get_current_raid(region)  # type: ignore
        )
        last_crawled_at = profile_data.get("last_crawled_at")
        char_last_updated = Raiderio.parse_date(last_crawled_at) if last_crawled_at else "N/A"
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
        wcl_url = f"https://www.warcraftlogs.com/character/{region}/{realm}/{char_name}"
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
            name=_("raider.io profile"),
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
        # if runs := Raiderio.get_all_runs(profile_data):
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
        #         name=_("raider.io profile"),
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
        embed = await Raiderio.make_gear_embed(
            char_gear,
            char_image,
            char_last_updated,
            char_name,
            char_score_color,
            char_url,
        )
        embeds.append(embed)

        ctx: Context = await Context.from_interaction(interaction)
        await ProfileMenu(pages=embeds, talents=char_talents, disable_after_timeout=True).start(
            ctx
        )

    @user_install_raiderio_profile.autocomplete("realm")
    async def raiderio_profile_realm_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        # Live realm index (slug values) instead of the old static classic realm list.
        return (await get_realms(self, current))[:25]

    @user_install_raiderio_profile.autocomplete("character")
    async def raiderio_profile_character_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        if not interaction.guild:
            return []
        roster = await self.config.guild(interaction.guild).guild_roster()
        matched_characters = []
        for character in roster.keys():
            char_name = character.split(":")[0]
            if current.lower() in char_name.lower():
                matched_characters.append(char_name)
        return [
            app_commands.Choice(name=char, value=char.lower()) for char in matched_characters[:25]
        ]

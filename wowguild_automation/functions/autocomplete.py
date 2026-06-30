from typing import List

import discord
from discord import app_commands


REGIONS = ["eu", "us", "kr", "tw"]
VERSIONS = ["retail", "classic", "classic_era", "mop_classic"]


async def region_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    _ = interaction
    return [
        app_commands.Choice(name=value, value=value)
        for value in REGIONS
        if current.lower() in value.lower()
    ][:25]


async def version_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    _ = interaction
    return [
        app_commands.Choice(name=value, value=value)
        for value in VERSIONS
        if current.lower() in value.lower()
    ][:25]


"""Top-level command groups shared by all WoWTools mixins.

Discord allows at most 100 global slash commands per bot. Instead of one top-level
slash command per feature, this cog registers only two:

* ``/wowt``     - user commands (character lookups, raider.io, token, scoreboard, ...)
* ``/wowtset``  - settings (server, guild management, scoreboard, own character, bot)

(plus the separate user-installable ``wowt-raiderio`` group).

The mixins attach their subcommands with ``@WoWToolsGroups.main_group.command(...)``
or ``@WoWToolsGroups.settings_group.group(...)``. Slash commands allow at most
group -> subgroup -> command, so nothing may be nested deeper than that.
The old top-level names still work as hidden prefix commands (see ``legacy.py``).
"""

from redbot.core import commands

# Command prefix of this cog: "wowt" (Retail) / "wowtc" (Classic).
PREFIX = "wowt"


class WoWToolsGroups:
    @commands.hybrid_group(
        name=PREFIX,
        description="WoW tools: characters, raider.io, token, scoreboard, guild.",
        extras={"i18n_desc": {
            "de-DE": "WoW-Tools: Charaktere, raider.io, Token, Scoreboard, Gilde.",
            "en-US": "WoW tools: characters, raider.io, token, scoreboard, guild.",
        }},
    )
    async def main_group(self, ctx: commands.Context):
        """WoW tools: characters, raider.io, token, scoreboard, guild."""
        pass

    @commands.hybrid_group(
        name=f"{PREFIX}set",
        description="WoWTools settings: server, guild, scoreboard, your character.",
        extras={"i18n_desc": {
            "de-DE": "WoWTools-Einstellungen: Server, Gilde, Scoreboard, eigener Charakter.",
            "en-US": "WoWTools settings: server, guild, scoreboard, your character.",
        }},
    )
    async def settings_group(self, ctx: commands.Context):
        """WoWTools settings: server, guild, scoreboard, your character."""
        pass

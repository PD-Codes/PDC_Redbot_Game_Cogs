import discord
from discord import app_commands
from discord.app_commands import AppCommandContext, AppInstallationType
from redbot.core.i18n import Translator

from ..forever_api import not_available_msg

_ = Translator("WoWToolsForever", __file__)


class UserInstallableRaiderio:
    # Raider.IO has no WoW Forever support; the user-installable command stays for
    # parity with the other WoWTools cogs and only answers with a notice.
    user_install_raiderio = app_commands.Group(
        name="wowtf-raiderio",
        description="Raider.IO (not available in WoW Forever)",
        allowed_installs=AppInstallationType(guild=True, user=True),
        allowed_contexts=AppCommandContext(guild=True, dm_channel=True, private_channel=True),
    )

    @user_install_raiderio.command(name="profile")
    @app_commands.describe(name="Character first name", surname="Character surname")
    async def user_install_raiderio_profile(
        self, interaction: discord.Interaction, name: str = "", surname: str = ""
    ) -> None:
        """Display the Raider.IO profile of a character (not available in WoW Forever)."""
        lang = "en-US"
        try:
            if interaction.guild is not None:
                lang = await self.config.guild(interaction.guild).language()
            elif str(getattr(interaction, "locale", "")).lower().startswith("de"):
                lang = "de-DE"
        except Exception:
            pass
        await interaction.response.send_message(
            not_available_msg(lang, "Raider.IO"), ephemeral=True
        )

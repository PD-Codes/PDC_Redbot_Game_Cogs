from redbot.core.bot import Red
from redbot.core.utils import get_end_user_data_statement

from .core import WarcraftLogsClassic

__red_end_user_data_statement__ = get_end_user_data_statement(__file__)


async def setup(bot: Red) -> None:
    cog = WarcraftLogsClassic(bot)
    await cog._create_client()
    await bot.add_cog(cog)

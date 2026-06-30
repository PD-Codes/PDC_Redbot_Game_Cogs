from .wowwatchlist import WoWWatchlist


async def setup(bot):
    await bot.add_cog(WoWWatchlist(bot))

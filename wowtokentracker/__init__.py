from .wowtokentracker import WoWTokenTracker


async def setup(bot):
    await bot.add_cog(WoWTokenTracker(bot))

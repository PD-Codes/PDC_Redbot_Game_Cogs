from .triviagame import TriviaGame


async def setup(bot):
    await bot.add_cog(TriviaGame(bot))

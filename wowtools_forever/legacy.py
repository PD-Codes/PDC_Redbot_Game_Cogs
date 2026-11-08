# Hidden prefix-only forwards from the old top-level command names (wowtf-charinfo,
# wowtf-wowset, ...) to the new /wowtf and /wowtfset groups, so existing habits and
# saved snippets keep working. None of these registers a slash command.
from copy import copy

from redbot.core import commands

_BOT_SETTINGS = {"blizzard", "emote", "assintegration", "status"}
_BLACKLIST_ACTIONS = {"add", "remove", "list", "clear"}


class LegacyRedirects:
    async def _legacy_forward(self, ctx: commands.Context, target: str) -> None:
        """Re-invoke the message as ``<prefix><target> <rest>``."""
        content = ctx.message.content
        start = len(ctx.prefix or "") + len(ctx.invoked_with or "")
        rest = content[start:].strip() if start <= len(content) else ""
        msg = copy(ctx.message)
        msg.content = f"{ctx.prefix}{target} {rest}".rstrip()
        new_ctx = await self.bot.get_context(msg)
        await self.bot.invoke(new_ctx)

    @staticmethod
    def _rest_words(ctx: commands.Context) -> list:
        content = ctx.message.content
        start = len(ctx.prefix or "") + len(ctx.invoked_with or "")
        return content[start:].split()

    # ---- /wowtf ---------------------------------------------------------- #
    @commands.command(name="wowtf-charinfo", hidden=True)
    async def legacy_charinfo(self, ctx: commands.Context):
        """Moved to `wowtf charinfo`."""
        await self._legacy_forward(ctx, "wowtf charinfo")

    @commands.command(name="wowtf-charstats", hidden=True)
    async def legacy_charstats(self, ctx: commands.Context):
        """Moved to `wowtf charstats`."""
        await self._legacy_forward(ctx, "wowtf charstats")

    @commands.command(name="wowtf-comparechars", hidden=True)
    async def legacy_comparechars(self, ctx: commands.Context):
        """Moved to `wowtf comparechars`."""
        await self._legacy_forward(ctx, "wowtf comparechars")

    @commands.command(name="wowtf-gearcheck", hidden=True)
    async def legacy_gearcheck(self, ctx: commands.Context):
        """Moved to `wowtf gearcheck`."""
        await self._legacy_forward(ctx, "wowtf gearcheck")

    @commands.command(name="wowtf-raidinfo", hidden=True)
    async def legacy_raidinfo(self, ctx: commands.Context):
        """Moved to `wowtf raidinfo`."""
        await self._legacy_forward(ctx, "wowtf raidinfo")

    @commands.command(name="wowtf-talentcheck", hidden=True)
    async def legacy_talentcheck(self, ctx: commands.Context):
        """Moved to `wowtf talentcheck`."""
        await self._legacy_forward(ctx, "wowtf talentcheck")

    @commands.command(name="wowtf-rating", hidden=True)
    async def legacy_rating(self, ctx: commands.Context):
        """Moved to `wowtf rating`."""
        await self._legacy_forward(ctx, "wowtf rating")

    @commands.command(name="wowtf-wowtoken", hidden=True)
    async def legacy_wowtoken(self, ctx: commands.Context):
        """Moved to `wowtf token`."""
        await self._legacy_forward(ctx, "wowtf token")

    @commands.command(name="wowtf-cvar", hidden=True)
    async def legacy_cvar(self, ctx: commands.Context):
        """Moved to `wowtf cvar`."""
        await self._legacy_forward(ctx, "wowtf cvar")

    @commands.command(name="wowtf-gmanage", hidden=True)
    async def legacy_gmanage(self, ctx: commands.Context):
        """Moved to `wowtf gmanage`."""
        await self._legacy_forward(ctx, "wowtf gmanage")

    @commands.command(name="wowtf-wowscoreboard", aliases=["wowtf-sb"], hidden=True)
    async def legacy_wowscoreboard(self, ctx: commands.Context):
        """Moved to `wowtf scoreboard`."""
        await self._legacy_forward(ctx, "wowtf scoreboard")

    @commands.command(name="wowtf-raiderio", aliases=["wowtf-rio"], hidden=True)
    async def legacy_raiderio(self, ctx: commands.Context):
        """Moved to `wowtf raiderio`."""
        await self._legacy_forward(ctx, "wowtf raiderio")

    # ---- /wowtfset ------------------------------------------------------- #
    @commands.command(name="wowtf-serverset", hidden=True)
    async def legacy_serverset(self, ctx: commands.Context):
        """Moved to `wowtfset server`."""
        await self._legacy_forward(ctx, "wowtfset server")

    @commands.command(name="wowtf-gmset", hidden=True)
    async def legacy_gmset(self, ctx: commands.Context):
        """Moved to `wowtfset guild`."""
        await self._legacy_forward(ctx, "wowtfset guild")

    @commands.command(name="wowtf-sbset", hidden=True)
    async def legacy_sbset(self, ctx: commands.Context):
        """Moved to `wowtfset scoreboard`."""
        words = self._rest_words(ctx)
        # Old: sbset blacklist|blocklist add|remove|list|clear -> new: blacklist-add, ...
        if words and words[0].lower() in ("blacklist", "blocklist"):
            action = words[1].lower() if len(words) > 1 else ""
            if action in _BLACKLIST_ACTIONS:
                target = f"wowtfset scoreboard blacklist-{action}"
                content = f"{ctx.prefix}{target} {' '.join(words[2:])}".rstrip()
            else:
                content = f"{ctx.prefix}help wowtfset scoreboard"
            msg = copy(ctx.message)
            msg.content = content
            new_ctx = await self.bot.get_context(msg)
            await self.bot.invoke(new_ctx)
            return
        await self._legacy_forward(ctx, "wowtfset scoreboard")

    @commands.command(name="wowtf-wowset", hidden=True)
    async def legacy_wowset(self, ctx: commands.Context):
        """Moved to `wowtfset me` (character) and `wowtfset bot` (owner settings)."""
        words = self._rest_words(ctx)
        first = words[0].lower() if words else ""
        if first == "character":
            # Old: wowset character name|surname|region ... -> new: wowtfset me ...
            content = ctx.message.content
            start = len(ctx.prefix or "") + len(ctx.invoked_with or "")
            rest = content[start:].lstrip()[len("character"):].strip()
            msg = copy(ctx.message)
            msg.content = f"{ctx.prefix}wowtfset me {rest}".rstrip()
            new_ctx = await self.bot.get_context(msg)
            await self.bot.invoke(new_ctx)
            return
        if first in _BOT_SETTINGS:
            await self._legacy_forward(ctx, "wowtfset bot")
            return
        await self._legacy_forward(ctx, "wowtfset")

"""Hidden prefix-only forwarders for the command names used before the regrouping.

``[p]wowt-charinfo ...`` keeps working and is re-dispatched as ``[p]wowt charinfo ...``
(likewise for all other former top-level commands). They have no slash version.
"""

from copy import copy
from typing import Callable, Optional

from redbot.core import commands

from .groups import PREFIX

_BLACKLIST_ACTIONS = ("add", "remove", "list", "clear")
_BOT_SETTINGS = ("blizzard", "emote", "assintegration", "status")


def _rewrite_wowset(rest: str) -> str:
    """old ``wowset character name X`` -> ``me name X``; ``wowset emote ..`` -> ``bot emote ..``"""
    parts = rest.split(maxsplit=1)
    head = parts[0].lower() if parts else ""
    tail = parts[1] if len(parts) > 1 else ""
    if head == "character":
        return f"me {tail}"
    if head in _BOT_SETTINGS:
        return f"bot {rest}"
    return rest


def _rewrite_sbset(rest: str) -> str:
    """old ``sbset blacklist add X`` -> ``blacklist-add X`` (slash allows no deeper nesting)."""
    parts = rest.split(maxsplit=2)
    if (
        len(parts) >= 2
        and parts[0].lower() in ("blacklist", "blocklist")
        and parts[1].lower() in _BLACKLIST_ACTIONS
    ):
        return " ".join([f"blacklist-{parts[1].lower()}", *parts[2:]])
    return rest


def _legacy(
    old: str,
    new: str,
    *,
    aliases: tuple = (),
    rewrite: Optional[Callable[[str], str]] = None,
):
    async def forward(self, ctx: commands.Context, *, rest: str = ""):
        if rewrite is not None:
            rest = rewrite(rest)
        await self._forward_legacy(ctx, new, rest)

    forward.__name__ = forward.__qualname__ = "legacy_" + old.replace("-", "_")
    forward.__doc__ = f"Moved to `[p]{new}`."
    return commands.command(name=old, aliases=list(aliases), hidden=True)(forward)


class LegacyCommands:
    async def _forward_legacy(self, ctx: commands.Context, new: str, rest: str) -> None:
        msg = copy(ctx.message)
        msg.content = f"{ctx.prefix}{new} {rest}".rstrip()
        new_ctx = await self.bot.get_context(msg)
        await self.bot.invoke(new_ctx)

    legacy_charinfo = _legacy(f"{PREFIX}-charinfo", f"{PREFIX} charinfo")
    legacy_charstats = _legacy(f"{PREFIX}-charstats", f"{PREFIX} charstats")
    legacy_comparechars = _legacy(f"{PREFIX}-comparechars", f"{PREFIX} comparechars")
    legacy_gearcheck = _legacy(f"{PREFIX}-gearcheck", f"{PREFIX} gearcheck")
    legacy_raidinfo = _legacy(f"{PREFIX}-raidinfo", f"{PREFIX} raidinfo")
    legacy_talentcheck = _legacy(f"{PREFIX}-talentcheck", f"{PREFIX} talentcheck")
    legacy_rating = _legacy(f"{PREFIX}-rating", f"{PREFIX} rating")
    legacy_wowtoken = _legacy(f"{PREFIX}-wowtoken", f"{PREFIX} token")
    legacy_cvar = _legacy(f"{PREFIX}-cvar", f"{PREFIX} cvar")
    legacy_raiderio = _legacy(
        f"{PREFIX}-raiderio", f"{PREFIX} raiderio", aliases=(f"{PREFIX}-rio",)
    )
    legacy_wowscoreboard = _legacy(
        f"{PREFIX}-wowscoreboard", f"{PREFIX} scoreboard", aliases=(f"{PREFIX}-sb",)
    )
    legacy_gmanage = _legacy(f"{PREFIX}-gmanage", f"{PREFIX} gmanage")
    legacy_serverset = _legacy(f"{PREFIX}-serverset", f"{PREFIX}set server")
    legacy_gmset = _legacy(f"{PREFIX}-gmset", f"{PREFIX}set guild")
    legacy_sbset = _legacy(f"{PREFIX}-sbset", f"{PREFIX}set scoreboard", rewrite=_rewrite_sbset)
    legacy_wowset = _legacy(f"{PREFIX}-wowset", f"{PREFIX}set", rewrite=_rewrite_wowset)

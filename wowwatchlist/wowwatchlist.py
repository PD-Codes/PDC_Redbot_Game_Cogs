"""WoWWatchlist — track WoW characters, post weekly M+ / raid progress.

Keeps a per-guild watchlist of characters and posts a weekly summary of their
Mythic+ score and raid progression via **raider.io** (no API key needed).
Characters are managed from the web dashboard (a table). Opt-in per guild,
bilingual (DE/EN).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import List, Optional, Tuple

import aiohttp
import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

from .pdc_dashboard import (
    Field,
    L,
    PanelSchema,
    SubmitResult,
    dashboard_list,
    dashboard_panel,
    register_dashboard,
    tr_lang,
    unregister_dashboard,
)

log = logging.getLogger("red.pdc.wowwatchlist")

_RIO = "https://raider.io/api/v1/characters/profile"
_REGIONS = ["eu", "us", "kr", "tw"]
_MAX_CHARS = 25  # characters per guild
_EMBED_LIMIT = 4000  # description chars per embed (hard limit is 4096)
_MAX_RAIDS_SHOWN = 3


def _slug(realm: str) -> str:
    """Slugify a realm name the way Blizzard/raider.io do.

    Apostrophes and parentheses are dropped, whitespace becomes a hyphen and
    accents are kept (raider.io uses e.g. ``aggra-português``). The aiohttp
    ``params`` mapping takes care of URL-encoding the result.
    """
    s = (realm or "").strip().lower()
    s = re.sub(r"[\'\u2019`()\[\]]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return re.sub(r"-{2,}", "-", s)


class WoWWatchlist(commands.Cog):
    """Track WoW characters and post weekly Mythic+ / raid progress."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x77A7C_11, force_registration=True)
        self.config.register_guild(
            enabled=False,
            language="en-US",
            channel=None,
            characters={},  # id -> {region, realm, name}
            interval_days=7,
            last_post=0.0,
        )
        self._task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        register_dashboard(self)
        self._task = asyncio.create_task(self._loop())

    def cog_unload(self) -> None:
        unregister_dashboard(self)
        if self._task:
            self._task.cancel()

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        """This cog does not store personal data about Discord users."""
        return

    @staticmethod
    def _t(lang: str, de: str, en: str) -> str:
        return de if str(lang).lower().startswith("de") else en

    async def _lang(self, guild) -> str:
        if guild is None:
            return "en-US"
        return await self.config.guild(guild).language()

    # ------------------------------------------------------------------ #
    # raider.io
    # ------------------------------------------------------------------ #
    async def _fetch(self, region: str, realm: str, name: str) -> Tuple[Optional[dict], str]:
        """Query raider.io. Returns ``(data, state)``.

        ``state`` is ``"ok"``, ``"notfound"`` (HTTP 400/404) or ``"error"``
        (timeout, 5xx, other failures). A 429 is retried once after
        ``Retry-After`` (capped at 10 s).
        """
        params = {
            "region": region,
            "realm": _slug(realm),
            "name": name,
            "fields": "mythic_plus_scores_by_season:current,raid_progression",
        }
        for attempt in range(2):
            delay = 0.0
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(_RIO, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                        if r.status == 200:
                            return await r.json(), "ok"
                        if r.status in (400, 404):
                            return None, "notfound"
                        if r.status == 429 and attempt == 0:
                            try:
                                delay = float(r.headers.get("Retry-After", 5))
                            except (TypeError, ValueError):
                                delay = 5.0
                            delay = max(1.0, min(delay, 10.0))
                        else:
                            log.warning("raider.io returned HTTP %s for %s-%s", r.status, realm, name)
                            return None, "error"
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                log.warning("raider.io fetch failed for %s-%s", realm, name, exc_info=True)
                return None, "error"
            await asyncio.sleep(delay)
        return None, "error"

    @staticmethod
    def _mplus(data: dict) -> float:
        try:
            return float(data["mythic_plus_scores_by_season"][0]["scores"]["all"])
        except Exception:
            return 0.0

    @staticmethod
    def _raid(data: dict) -> str:
        """Summarize raid progress.

        raider.io gives no reliable ordering of the raid keys, so every raid
        with progress is listed (at most a few) instead of guessing "the latest".
        """
        try:
            prog = data.get("raid_progression") or {}
            parts = []
            for key, val in prog.items():
                if not isinstance(val, dict):
                    continue
                killed = sum(int(val.get(f"{d}_bosses_killed", 0) or 0) for d in ("normal", "heroic", "mythic"))
                if killed > 0:
                    parts.append((str(key), str(val.get("summary", "—"))))
            if not parts:
                return "—"
            if len(parts) == 1:
                return parts[0][1]
            shown = [f"{k.replace('-', ' ')}: {v}" for k, v in parts[:_MAX_RAIDS_SHOWN]]
            if len(parts) > _MAX_RAIDS_SHOWN:
                shown.append(f"+{len(parts) - _MAX_RAIDS_SHOWN}")
            return " · ".join(shown)
        except Exception:
            return "—"

    async def _build_embeds(self, guild, lang: str) -> Tuple[Optional[List[discord.Embed]], bool]:
        """Build the summary embeds. Returns ``(embeds, all_failed)``.

        ``embeds`` is ``None`` without characters. ``all_failed`` is True if every
        lookup failed with a technical error (so the weekly post is not worth sending).
        """
        chars = await self.config.guild(guild).characters()
        if not chars:
            return None, False
        sem = asyncio.Semaphore(5)

        async def one(c: dict):
            async with sem:
                return await self._fetch(c.get("region", "eu"), c.get("realm", ""), c.get("name", ""))

        results = await asyncio.gather(*(one(c) for c in chars.values()))
        rows = []
        errors = 0
        for c, (data, state) in zip(chars.values(), results):
            if state == "ok" and data:
                score = self._mplus(data)
                raid = self._raid(data)
                url = data.get("profile_url", "")
                rows.append((score, f"[**{data.get('name', c.get('name'))}**]({url}) · M+ **{score:.0f}** · {raid}"))
            elif state == "error":
                errors += 1
                rows.append((-1.0, f"⚠️ **{c.get('name')}** ({c.get('realm')}) — {self._t(lang, 'Abfrage fehlgeschlagen', 'lookup failed')}"))
            else:
                rows.append((-1.0, f"❔ **{c.get('name')}** ({c.get('realm')}) — {self._t(lang, 'nicht gefunden', 'not found')}"))
        rows.sort(key=lambda x: x[0], reverse=True)
        # Split into several embeds instead of truncating.
        pages: List[List[str]] = [[]]
        size = 0
        for _, line in rows:
            if pages[-1] and size + len(line) + 1 > _EMBED_LIMIT:
                pages.append([])
                size = 0
            pages[-1].append(line[:_EMBED_LIMIT])
            size += len(line) + 1
        title = self._t(lang, "📊 WoW-Watchlist — Wochenüberblick", "📊 WoW Watchlist — weekly overview")
        embeds = []
        for i, lines in enumerate(pages):
            e = discord.Embed(
                title=title if i == 0 else f"{title} ({i + 1}/{len(pages)})",
                description="\n".join(lines),
                colour=discord.Colour.dark_gold(),
            )
            if i == len(pages) - 1:
                e.timestamp = discord.utils.utcnow()
            embeds.append(e)
        return embeds, errors == len(rows)

    # ------------------------------------------------------------------ #
    # Weekly loop
    # ------------------------------------------------------------------ #
    async def _loop(self) -> None:
        await self.bot.wait_until_red_ready()
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("WoWWatchlist tick failed")
            await asyncio.sleep(3600)  # check hourly; posts at most once per interval

    async def _tick(self) -> None:
        now = time.time()
        guilds = await self.config.all_guilds()
        for gid, gconf in guilds.items():
            try:
                await self._tick_guild(gid, gconf, now)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("WoWWatchlist tick failed for guild %s", gid)

    async def _tick_guild(self, gid: int, gconf: dict, now: float) -> None:
        if not gconf.get("enabled") or not gconf.get("channel") or not gconf.get("characters"):
            return
        interval = int(gconf.get("interval_days", 7) or 7) * 86400
        if now - float(gconf.get("last_post", 0)) < interval:
            return
        guild = self.bot.get_guild(gid)
        if guild is None:
            return
        cid = gconf.get("channel")
        channel = guild.get_channel_or_thread(cid) if hasattr(guild, "get_channel_or_thread") else guild.get_channel(cid)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        perms = channel.permissions_for(guild.me)
        if not (perms.send_messages and perms.embed_links):
            log.warning("No send/embed permission in channel %s (guild %s)", cid, gid)
            return
        embeds, all_failed = await self._build_embeds(guild, gconf.get("language", "en-US"))
        if not embeds or all_failed:
            return  # nothing to post, or raider.io is down: retry next hour
        sent = 0
        for embed in embeds:
            try:
                await channel.send(embed=embed)
                sent += 1
            except discord.HTTPException:
                log.warning("Could not post watchlist summary in channel %s (guild %s)", cid, gid, exc_info=True)
                break
        if sent:
            await self.config.guild(guild).last_post.set(now)

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #
    @commands.hybrid_group(name="watchlist", aliases=["wowwatch"])
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def watchlist(self, ctx: commands.Context) -> None:
        """Configure the WoW character watchlist."""

    @watchlist.command(name="enable")
    @app_commands.describe(on_off="Enable or disable the watchlist")
    async def w_enable(self, ctx: commands.Context, on_off: bool) -> None:
        """Enable/disable the module for this server."""
        lang = await self._lang(ctx.guild)
        await self.config.guild(ctx.guild).enabled.set(on_off)
        state = self._t(lang, "aktiviert" if on_off else "deaktiviert", "enabled" if on_off else "disabled")
        await ctx.send(self._t(lang, f"Watchlist **{state}**.", f"Watchlist **{state}**."))

    @watchlist.command(name="channel")
    @app_commands.describe(channel="Channel for the weekly summary")
    async def w_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Set the channel for the weekly summary."""
        lang = await self._lang(ctx.guild)
        await self.config.guild(ctx.guild).channel.set(channel.id)
        await ctx.send(self._t(lang, f"Kanal: {channel.mention}", f"Channel: {channel.mention}"))

    @watchlist.command(name="add")
    @app_commands.describe(region="eu/us/kr/tw", realm="Character realm", name="Character name")
    async def w_add(self, ctx: commands.Context, region: str, realm: str, name: str) -> None:
        """Add a character to the watchlist."""
        lang = await self._lang(ctx.guild)
        region = region.lower()
        if region not in _REGIONS:
            await ctx.send(self._t(lang, "Region: eu, us, kr oder tw.", "Region: eu, us, kr or tw."))
            return
        await ctx.typing()
        data, state = await self._fetch(region, realm, name)
        if state == "error":
            await ctx.send(self._t(lang, "raider.io-Abfrage fehlgeschlagen, bitte später erneut versuchen.", "raider.io lookup failed, please try again later."))
            return
        if not data:
            await ctx.send(self._t(lang, "Charakter nicht gefunden (Region/Realm/Name prüfen).", "Character not found (check region/realm/name)."))
            return
        real_name = data.get("name", name)
        result = await self._add_char(ctx.guild, region, realm.strip(), real_name)
        if result == "limit":
            await ctx.send(self._t(lang, f"Limit erreicht (max. {_MAX_CHARS} Charaktere).", f"Limit reached (max {_MAX_CHARS} characters)."))
        elif result == "dup":
            await ctx.send(self._t(lang, "Charakter ist bereits in der Watchlist.", "Character is already on the watchlist."))
        else:
            await ctx.send(self._t(lang, f"Hinzugefügt: **{real_name}** ({realm}).", f"Added: **{real_name}** ({realm})."))

    async def _add_char(self, guild, region: str, realm: str, name: str) -> str:
        """Add a character; returns "ok", "dup" or "limit"."""
        key = (region, _slug(realm), name.strip().lower())
        async with self.config.guild(guild).characters() as chars:
            for c in chars.values():
                if (str(c.get("region", "")).lower(), _slug(str(c.get("realm", ""))), str(c.get("name", "")).strip().lower()) == key:
                    return "dup"
            if len(chars) >= _MAX_CHARS:
                return "limit"
            chars[uuid.uuid4().hex[:8]] = {"region": region, "realm": realm, "name": name}
        return "ok"

    @watchlist.command(name="remove")
    @app_commands.describe(name="Character name to remove")
    async def w_remove(self, ctx: commands.Context, *, name: str) -> None:
        """Remove a character by name."""
        lang = await self._lang(ctx.guild)
        removed = False
        async with self.config.guild(ctx.guild).characters() as chars:
            for cid, c in list(chars.items()):
                if str(c.get("name", "")).lower() == name.strip().lower():
                    del chars[cid]
                    removed = True
        await ctx.send(self._t(lang, "Entfernt." if removed else "Nicht gefunden.", "Removed." if removed else "Not found."))

    @watchlist.command(name="list")
    async def w_list(self, ctx: commands.Context) -> None:
        """List the watched characters."""
        lang = await self._lang(ctx.guild)
        chars = await self.config.guild(ctx.guild).characters()
        if not chars:
            await ctx.send(self._t(lang, "Keine Charaktere.", "No characters."))
            return
        body = "\n".join(f"`{cid}` · **{c.get('name')}** — {c.get('realm')} ({str(c.get('region','')).upper()})" for cid, c in chars.items())
        await ctx.send(embed=discord.Embed(title=self._t(lang, "Watchlist", "Watchlist"), description=body[:4000], colour=await ctx.embed_colour()))

    @watchlist.command(name="post")
    @commands.cooldown(1, 60, commands.BucketType.guild)
    async def w_post(self, ctx: commands.Context) -> None:
        """Post the summary now (also resets the weekly timer)."""
        lang = await self._lang(ctx.guild)
        await ctx.typing()
        embeds, _ = await self._build_embeds(ctx.guild, lang)
        if not embeds:
            await ctx.send(self._t(lang, "Keine Charaktere.", "No characters."))
            return
        sent = 0
        try:
            for embed in embeds:
                await ctx.send(embed=embed)
                sent += 1
        except discord.HTTPException:
            log.warning("Could not post watchlist summary in guild %s", ctx.guild.id, exc_info=True)
        if sent:
            await self.config.guild(ctx.guild).last_post.set(time.time())

    @watchlist.command(name="language")
    @app_commands.describe(language="Output language: de-DE or en-US")
    async def w_language(self, ctx: commands.Context, language: str) -> None:
        """Set the output language for this server."""
        language = "de-DE" if language.lower().startswith("de") else "en-US"
        await self.config.guild(ctx.guild).language.set(language)
        await ctx.send(self._t(language, "Sprache: Deutsch", "Language: English"))

    # ------------------------------------------------------------------ #
    # Dashboard panel + character table
    # ------------------------------------------------------------------ #
    @dashboard_panel("wowwatchlist", L("WoW-Watchlist", "WoW watchlist"), mount="guild_settings", permission="guild_admin", order=58)
    async def settings_panel(self, ctx):
        conf = self.config.guild(ctx.guild)
        lang = await conf.language()
        n = len(await conf.characters())
        return PanelSchema(
            description=tr_lang(
                lang,
                f"Wöchentlicher M+/Raid-Überblick via raider.io. {n} Charaktere. Verwalten im Tab 'Charaktere'.",
                f"Weekly M+/raid overview via raider.io. {n} characters. Manage them in the 'Characters' tab.",
            ),
            fields=[
                Field.switch("enabled", L("Aktiviert", "Enabled"), value=bool(await conf.enabled())),
                Field.channel("channel", L("Kanal", "Channel"), value=str(await conf.channel() or "")),
                Field.number("interval_days", L("Intervall (Tage)", "Interval (days)"), value=int(await conf.interval_days())),
                Field.select(
                    "language", L("Sprache", "Language"),
                    [{"value": "de-DE", "label": "Deutsch"}, {"value": "en-US", "label": "English"}],
                    value=str(lang), reload_on_change=True,
                ),
            ],
        )

    @settings_panel.on_submit
    async def _save_settings(self, ctx, data):
        conf = self.config.guild(ctx.guild)
        await conf.enabled.set(bool(data.get("enabled")))
        ch = str(data.get("channel") or "").strip()
        await (conf.channel.set(int(ch)) if ch.isdigit() else conf.channel.clear())
        try:
            days = int(data.get("interval_days", 7))
        except (TypeError, ValueError):
            days = 7
        await conf.interval_days.set(max(1, days))
        lang = str(data.get("language", "en-US")).strip() or "en-US"
        await conf.language.set(lang)
        return SubmitResult.ok(tr_lang(lang, "Gespeichert.", "Saved."))

    @dashboard_list(
        "characters", L("Charaktere", "Characters"), mount="guild_settings", permission="guild_admin", order=60,
        columns=[{"key": "name", "label": "Name"}, {"key": "realm", "label": "Realm"}, {"key": "region", "label": "Region"}],
        description=L("Charaktere der Watchlist. Neue im Tab 'Charakter hinzufügen'.", "Watchlist characters. Add new ones in the 'Add character' tab."),
    )
    async def chars_list(self, ctx):
        chars = await self.config.guild(ctx.guild).characters()
        return [
            {"id": cid, "cells": {"name": str(c.get("name", "")), "realm": str(c.get("realm", "")), "region": str(c.get("region", "")).upper()}}
            for cid, c in chars.items()
        ]

    @chars_list.edit_form
    async def chars_edit_form(self, ctx, item_id):
        chars = await self.config.guild(ctx.guild).characters()
        c = chars.get(item_id) or {}
        return PanelSchema(fields=[
            Field.text("name", L("Name", "Name"), value=str(c.get("name", ""))),
            Field.text("realm", L("Realm", "Realm"), value=str(c.get("realm", ""))),
            Field.select("region", L("Region", "Region"), [{"value": r, "label": r.upper()} for r in _REGIONS], value=str(c.get("region", "eu"))),
        ])

    @chars_list.on_edit
    async def chars_edit(self, ctx, item_id, data):
        lang = await self.config.guild(ctx.guild).language()
        async with self.config.guild(ctx.guild).characters() as chars:
            if item_id not in chars:
                return SubmitResult.fail(tr_lang(lang, "Nicht gefunden.", "Not found."))
            c = chars[item_id]
            c["name"] = str(data.get("name") or "").strip() or c.get("name", "")
            c["realm"] = str(data.get("realm") or "").strip() or c.get("realm", "")
            reg = str(data.get("region") or "eu").lower()
            c["region"] = reg if reg in _REGIONS else "eu"
            chars[item_id] = c
        return SubmitResult.ok(tr_lang(lang, "Charakter gespeichert.", "Character saved."))

    @chars_list.on_delete
    async def chars_delete(self, ctx, item_id):
        lang = await self.config.guild(ctx.guild).language()
        async with self.config.guild(ctx.guild).characters() as chars:
            chars.pop(item_id, None)
        return SubmitResult.ok(tr_lang(lang, "Charakter gelöscht.", "Character deleted."))

    @dashboard_panel("wowchar_add", L("Charakter hinzufügen", "Add character"), mount="guild_settings", permission="guild_admin", order=59)
    async def char_add_panel(self, ctx):
        lang = await self.config.guild(ctx.guild).language()
        return PanelSchema(
            description=tr_lang(lang, "Neuen Charakter zur Watchlist hinzufügen.", "Add a new character to the watchlist."),
            fields=[
                Field.text("name", L("Name", "Name"), value=""),
                Field.text("realm", L("Realm", "Realm"), value=""),
                Field.select("region", L("Region", "Region"), [{"value": r, "label": r.upper()} for r in _REGIONS], value="eu"),
            ],
        )

    @char_add_panel.on_submit
    async def _char_add(self, ctx, data):
        lang = await self.config.guild(ctx.guild).language()
        name = str(data.get("name") or "").strip()
        realm = str(data.get("realm") or "").strip()
        region = str(data.get("region") or "eu").lower()
        if not name or not realm:
            return SubmitResult.fail(tr_lang(lang, "Name und Realm erforderlich.", "Name and realm required."))
        result = await self._add_char(ctx.guild, region if region in _REGIONS else "eu", realm, name)
        if result == "limit":
            return SubmitResult.fail(tr_lang(lang, f"Limit erreicht (max. {_MAX_CHARS} Charaktere).", f"Limit reached (max {_MAX_CHARS} characters)."))
        if result == "dup":
            return SubmitResult.fail(tr_lang(lang, "Charakter ist bereits in der Watchlist.", "Character is already on the watchlist."))
        return SubmitResult.ok(tr_lang(lang, "Charakter hinzugefügt.", "Character added."), reload=True)

"""WoWWatchlist — track WoW characters, post weekly M+ / raid progress.

Keeps a per-guild watchlist of characters and posts a weekly summary of their
Mythic+ score and raid progression via **raider.io** (no API key needed).
Characters are managed from the web dashboard (a table). Opt-in per guild,
bilingual (DE/EN).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import List, Optional

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


def _slug(realm: str) -> str:
    return (realm or "").strip().lower().replace(" ", "-").replace("'", "")


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
    async def _fetch(self, region: str, realm: str, name: str) -> Optional[dict]:
        params = {
            "region": region,
            "realm": _slug(realm),
            "name": name,
            "fields": "mythic_plus_scores_by_season:current,raid_progression",
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(_RIO, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        return None
                    return await r.json()
        except Exception:
            log.debug("raider.io fetch failed for %s-%s", realm, name, exc_info=True)
            return None

    @staticmethod
    def _mplus(data: dict) -> float:
        try:
            return float(data["mythic_plus_scores_by_season"][0]["scores"]["all"])
        except Exception:
            return 0.0

    @staticmethod
    def _raid(data: dict) -> str:
        try:
            prog = data.get("raid_progression") or {}
            if not prog:
                return "—"
            last = list(prog.values())[-1]
            return str(last.get("summary", "—"))
        except Exception:
            return "—"

    async def _build_embed(self, guild, lang: str) -> Optional[discord.Embed]:
        chars = await self.config.guild(guild).characters()
        if not chars:
            return None
        rows = []
        for c in chars.values():
            data = await self._fetch(c.get("region", "eu"), c.get("realm", ""), c.get("name", ""))
            if not data:
                rows.append((0.0, f"❔ **{c.get('name')}** ({c.get('realm')}) — {self._t(lang, 'nicht gefunden', 'not found')}"))
                continue
            score = self._mplus(data)
            raid = self._raid(data)
            url = data.get("profile_url", "")
            rows.append((score, f"[**{data.get('name', c.get('name'))}**]({url}) · M+ **{score:.0f}** · {raid}"))
        rows.sort(key=lambda x: x[0], reverse=True)
        e = discord.Embed(
            title=self._t(lang, "📊 WoW-Watchlist — Wochenüberblick", "📊 WoW Watchlist — weekly overview"),
            description="\n".join(r[1] for r in rows)[:4000],
            colour=discord.Colour.dark_gold(),
            timestamp=discord.utils.utcnow(),
        )
        return e

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
            if not gconf.get("enabled") or not gconf.get("channel") or not gconf.get("characters"):
                continue
            interval = int(gconf.get("interval_days", 7) or 7) * 86400
            if now - float(gconf.get("last_post", 0)) < interval:
                continue
            guild = self.bot.get_guild(gid)
            if guild is None:
                continue
            channel = guild.get_channel(gconf.get("channel"))
            if channel is None or not channel.permissions_for(guild.me).send_messages:
                continue
            await self.config.guild(guild).last_post.set(now)
            embed = await self._build_embed(guild, gconf.get("language", "en-US"))
            if embed is not None:
                try:
                    await channel.send(embed=embed)
                except discord.HTTPException:
                    pass

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
        data = await self._fetch(region, realm, name)
        if not data:
            await ctx.send(self._t(lang, "Charakter nicht gefunden (Region/Realm/Name prüfen).", "Character not found (check region/realm/name)."))
            return
        cid = uuid.uuid4().hex[:8]
        async with self.config.guild(ctx.guild).characters() as chars:
            chars[cid] = {"region": region, "realm": realm.strip(), "name": data.get("name", name)}
        await ctx.send(self._t(lang, f"Hinzugefügt: **{data.get('name', name)}** ({realm}).", f"Added: **{data.get('name', name)}** ({realm})."))

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
    async def w_post(self, ctx: commands.Context) -> None:
        """Post the summary now (also resets the weekly timer)."""
        lang = await self._lang(ctx.guild)
        await ctx.typing()
        embed = await self._build_embed(ctx.guild, lang)
        if embed is None:
            await ctx.send(self._t(lang, "Keine Charaktere.", "No characters."))
            return
        await self.config.guild(ctx.guild).last_post.set(time.time())
        await ctx.send(embed=embed)

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
            c = chars.get(item_id) or {}
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
        cid = uuid.uuid4().hex[:8]
        async with self.config.guild(ctx.guild).characters() as chars:
            chars[cid] = {"region": region if region in _REGIONS else "eu", "realm": realm, "name": name}
        return SubmitResult.ok(tr_lang(lang, "Charakter hinzugefügt.", "Character added."), reload=True)

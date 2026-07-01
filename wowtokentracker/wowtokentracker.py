"""WoWTokenTracker — record the WoW Token price over time and show history.

A companion cog (kept separate from WoWTools to stay simple). It periodically
records the token price per region for **retail** and optionally **classic**,
and the ``wowtoken`` command shows the current price plus 24h/7d change and
min/max. Uses the shared Blizzard API key
(`[p]set api blizzard client_id,<id> client_secret,<secret>`). Bilingual (DE/EN).

A dashboard chart is planned as the next step.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import List, Optional

import aiohttp
import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

from .pdc_dashboard import (
    Component,
    Control,
    L,
    PageSchema,
    WidgetData,
    dashboard_page,
    dashboard_widget,
    register_dashboard,
    tr,
    unregister_dashboard,
)

log = logging.getLogger("red.pdc.wowtokentracker")

_API_HOST = {"eu": "eu.api.blizzard.com", "us": "us.api.blizzard.com", "kr": "kr.api.blizzard.com", "tw": "kr.api.blizzard.com"}
_AUTH_HOST = {"eu": "eu.battle.net", "us": "us.battle.net", "kr": "apac.battle.net", "tw": "apac.battle.net"}
_REGIONS = ["eu", "us", "kr", "tw"]
_CAP = 720  # samples kept per series (~30 days hourly)
_INTERVAL = 3600  # seconds between samples


class WoWTokenTracker(commands.Cog):
    """Track the WoW Token price over time (retail + classic)."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x70E_71_C, force_registration=True)
        self.config.register_global(
            enabled=True,
            regions=["eu"],
            classic=False,
            history={},  # "game:region" -> [[ts, price_copper], ...]
            language="en-US",
        )
        self._task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        register_dashboard(self)
        self._task = asyncio.create_task(self._loop())

    def cog_unload(self) -> None:
        unregister_dashboard(self)
        if self._task:
            self._task.cancel()

    @staticmethod
    def _t(lang: str, de: str, en: str) -> str:
        return de if str(lang).lower().startswith("de") else en

    @staticmethod
    def _gold(copper: int) -> str:
        return f"{int(copper) // 10000:,}g"

    # ------------------------------------------------------------------ #
    # Dashboard: token price chart widget
    # ------------------------------------------------------------------ #
    @dashboard_widget("wowtoken_chart", L("WoW Token (Verlauf)", "WoW Token (history)"), size="lg", refresh=600, permission="authenticated")
    async def token_chart(self, ctx):
        hist = await self.config.history()
        lang = await self.config.language()
        if not hist:
            return WidgetData.markdown(self._t(lang, "Noch keine Token-Daten gesammelt.", "No token data collected yet."))
        # Labels from the longest series; all series captured at the same ticks so
        # they align by index (shorter ones are padded with leading gaps).
        longest = max(hist, key=lambda k: len(hist[k]))
        base = hist[longest]
        labels = [datetime.datetime.utcfromtimestamp(int(ts)).strftime("%m-%d %H:%M") for ts, _ in base]
        n = len(labels)
        series = []
        for key, points in hist.items():
            data = [round(int(p) / 10000) for _, p in points]
            data = [None] * max(0, n - len(data)) + data[-n:]
            series.append({"label": key, "data": data})
        return WidgetData.chart(series, chart_type="line", labels=labels)

    # ------------------------------------------------------------------ #
    # Dashboard: full page (retail + classic charts, region dropdown)
    # ------------------------------------------------------------------ #
    @dashboard_page(
        "tokens",
        L("WoW Token", "WoW Token"),
        scope="global",
        permission="authenticated",
        icon="coins",
        description=L("Token-Preisverlauf (Retail & Classic)", "Token price history (retail & classic)"),
    )
    async def token_page(self, ctx):
        hist = await self.config.history()
        tracked = await self.config.regions()
        # Regions that actually have data (retail or classic); fall back to tracked/config.
        regions = [r for r in _REGIONS if any(k.endswith(f":{r}") for k in hist)]
        if not regions:
            regions = tracked or ["eu"]
        sel = (ctx.params or {}).get("region") or regions[0]
        if sel not in regions:
            sel = regions[0]

        controls = [
            Control.select(
                "region",
                L("Region", "Region"),
                [{"value": r, "label": r.upper()} for r in regions],
                value=sel,
            )
        ]

        comps = [Component.heading(f"WoW Token \u2014 {sel.upper()}")]
        for game in ("retail", "classic"):
            points = hist.get(f"{game}:{sel}") or []
            title = "Retail" if game == "retail" else "Classic"
            if points:
                labels = [
                    datetime.datetime.utcfromtimestamp(int(ts)).strftime("%m-%d %H:%M")
                    for ts, _ in points
                ]
                data = [round(int(p) / 10000) for _, p in points]
                comps.append(
                    Component.chart(
                        labels=labels,
                        series=[{"label": title, "data": data}],
                        title=title,
                        height=280,
                    )
                )
            else:
                comps.append(
                    Component.text(
                        tr(
                            ctx,
                            f"Keine {title}-Daten für {sel.upper()}.",
                            f"No {title} data for {sel.upper()}.",
                        )
                    )
                )
        return PageSchema(components=comps, controls=controls)

    # ------------------------------------------------------------------ #
    # Blizzard API
    # ------------------------------------------------------------------ #
    async def _access_token(self, region: str) -> Optional[str]:
        tokens = await self.bot.get_shared_api_tokens("blizzard")
        cid, secret = tokens.get("client_id"), tokens.get("client_secret")
        if not cid or not secret:
            return None
        host = _AUTH_HOST.get(region, "eu.battle.net")
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"https://{host}/oauth/token",
                    data={"grant_type": "client_credentials"},
                    auth=aiohttp.BasicAuth(cid, secret),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    js = await r.json()
            return js.get("access_token")
        except Exception:
            log.debug("oauth failed for %s", region, exc_info=True)
            return None

    async def _fetch_price(self, region: str, game: str) -> Optional[int]:
        token = await self._access_token(region)
        if not token:
            return None
        host = _API_HOST.get(region)
        if not host:
            return None
        namespace = f"dynamic-{region}" if game == "retail" else f"dynamic-classic-{region}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"https://{host}/data/wow/token/index",
                    params={"namespace": namespace, "locale": "en_US"},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    if r.status != 200:
                        return None
                    js = await r.json()
            return int(js.get("price")) if js.get("price") is not None else None
        except Exception:
            log.debug("price fetch failed %s/%s", region, game, exc_info=True)
            return None

    # ------------------------------------------------------------------ #
    # Capture loop
    # ------------------------------------------------------------------ #
    async def _loop(self) -> None:
        await self.bot.wait_until_red_ready()
        while True:
            try:
                await self._capture()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("WoWTokenTracker capture failed")
            await asyncio.sleep(_INTERVAL)

    async def _capture(self) -> None:
        if not await self.config.enabled():
            return
        regions = await self.config.regions()
        games = ["retail"] + (["classic"] if await self.config.classic() else [])
        now = int(time.time())
        async with self.config.history() as hist:
            for region in regions:
                for game in games:
                    price = await self._fetch_price(region, game)
                    if price is None:
                        continue
                    key = f"{game}:{region}"
                    series = hist.get(key) or []
                    series.append([now, price])
                    hist[key] = series[-_CAP:]

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #
    @commands.hybrid_command(name="wowtoken", aliases=["token"])
    @app_commands.describe(region="eu / us / kr / tw", game="retail or classic")
    async def wowtoken(self, ctx: commands.Context, region: str = "eu", game: str = "retail") -> None:
        """Show the current WoW Token price and recent history."""
        lang = await self.config.language()
        region = region.lower()
        game = "classic" if game.lower().startswith("c") else "retail"
        if region not in _REGIONS:
            await ctx.send(self._t(lang, "Region: eu, us, kr oder tw.", "Region: eu, us, kr or tw."))
            return
        await ctx.typing()
        current = await self._fetch_price(region, game)
        series = (await self.config.history()).get(f"{game}:{region}") or []
        if current is None and not series:
            await ctx.send(self._t(lang, "Keinen Preis bekommen (Blizzard-Key gesetzt?).", "Couldn't get a price (Blizzard key set?)."))
            return
        if current is None and series:
            current = series[-1][1]

        def _ago(samples_back: int) -> Optional[int]:
            if len(series) > samples_back:
                return series[-1 - samples_back][1]
            return None

        prices = [p for _, p in series] + [current]
        lo, hi = min(prices), max(prices)
        e = discord.Embed(
            title=self._t(lang, f"WoW Token — {region.upper()} ({game})", f"WoW Token — {region.upper()} ({game})"),
            colour=discord.Colour.gold(),
        )
        e.add_field(name=self._t(lang, "Aktuell", "Current"), value=self._gold(current), inline=True)
        d1 = _ago(24)
        d7 = _ago(168)
        if d1 is not None:
            diff = current - d1
            e.add_field(name="24h", value=f"{'+' if diff >= 0 else ''}{self._gold(abs(diff)) if diff < 0 else self._gold(diff)}", inline=True)
        if d7 is not None:
            diff = current - d7
            e.add_field(name="7d", value=f"{'+' if diff >= 0 else ''}{self._gold(diff) if diff >= 0 else '-' + self._gold(abs(diff))}", inline=True)
        e.add_field(name=self._t(lang, "Min / Max (Verlauf)", "Min / Max (history)"), value=f"{self._gold(lo)} / {self._gold(hi)}", inline=False)
        e.set_footer(text=self._t(lang, f"{len(series)} Datenpunkte gespeichert", f"{len(series)} samples stored"))
        await ctx.send(embed=e)

    @commands.hybrid_group(name="wowtokenset")
    @commands.is_owner()
    async def wowtokenset(self, ctx: commands.Context) -> None:
        """Configure the token tracker (owner)."""

    @wowtokenset.command(name="region")
    @app_commands.describe(region="Region to toggle tracking for (eu/us/kr/tw)")
    async def wt_region(self, ctx: commands.Context, region: str) -> None:
        """Toggle a tracked region."""
        lang = await self.config.language()
        region = region.lower()
        if region not in _REGIONS:
            await ctx.send(self._t(lang, "Ungültige Region.", "Invalid region."))
            return
        async with self.config.regions() as regions:
            if region in regions:
                regions.remove(region)
                msg = self._t(lang, f"{region.upper()} wird nicht mehr getrackt.", f"No longer tracking {region.upper()}.")
            else:
                regions.append(region)
                msg = self._t(lang, f"{region.upper()} wird jetzt getrackt.", f"Now tracking {region.upper()}.")
        await ctx.send(msg)

    @wowtokenset.command(name="classic")
    @app_commands.describe(on_off="Also track classic token prices")
    async def wt_classic(self, ctx: commands.Context, on_off: bool) -> None:
        """Toggle tracking classic token prices too."""
        lang = await self.config.language()
        await self.config.classic.set(on_off)
        await ctx.send(self._t(lang, "Gespeichert.", "Saved."))

    @wowtokenset.command(name="language")
    @app_commands.describe(language="Output language: de-DE or en-US")
    async def wt_language(self, ctx: commands.Context, language: str) -> None:
        """Set the output language."""
        language = "de-DE" if language.lower().startswith("de") else "en-US"
        await self.config.language.set(language)
        await ctx.send(self._t(language, "Sprache: Deutsch", "Language: English"))

    @wowtokenset.command(name="status")
    async def wt_status(self, ctx: commands.Context) -> None:
        """Show tracker status."""
        lang = await self.config.language()
        regions = await self.config.regions()
        classic = await self.config.classic()
        hist = await self.config.history()
        tokens = await self.bot.get_shared_api_tokens("blizzard")
        keyed = bool(tokens.get("client_id") and tokens.get("client_secret"))
        e = discord.Embed(title=self._t(lang, "Token-Tracker", "Token tracker"), colour=await ctx.embed_colour())
        e.add_field(name=self._t(lang, "Regionen", "Regions"), value=", ".join(r.upper() for r in regions) or "—", inline=True)
        e.add_field(name="Classic", value="✅" if classic else "❌", inline=True)
        e.add_field(name=self._t(lang, "Blizzard-Key", "Blizzard key"), value="✅" if keyed else "❌", inline=True)
        e.add_field(name=self._t(lang, "Serien", "Series"), value="\n".join(f"{k}: {len(v)}" for k, v in hist.items()) or "—", inline=False)
        await ctx.send(embed=e)

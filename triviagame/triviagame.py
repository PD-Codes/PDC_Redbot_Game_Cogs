"""TriviaGame — a quiz game with its own question DB and a leaderboard.

Command group is ``quiz`` (not ``trivia``) to avoid clashing with Red's core
Trivia cog. Questions live in a per-guild database editable from the web
dashboard (a table with add/edit/delete). Opt-in per guild, bilingual (DE/EN).
"""
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from typing import Dict, List, Optional

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

log = logging.getLogger("red.pdc.triviagame")

ANSWER_TIME = 25  # seconds per question

_DEFAULT_QUESTIONS = [
    {"q": "What is the capital of France?", "a": "Paris", "alts": []},
    {"q": "How many continents are there?", "a": "7", "alts": ["seven"]},
    {"q": "What planet is known as the Red Planet?", "a": "Mars", "alts": []},
    {"q": "What is 9 x 8?", "a": "72", "alts": []},
    {"q": "Which language is this bot framework written in?", "a": "Python", "alts": []},
]


class TriviaGame(commands.Cog):
    """Trivia/quiz game with a question DB and a leaderboard."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x7217_1A, force_registration=True)
        self.config.register_guild(enabled=True, language="en-US", questions=_DEFAULT_QUESTIONS)
        self.config.register_member(points=0)
        self._active: Dict[int, dict] = {}  # channel_id -> {answers:set, winner, event}

    async def cog_load(self) -> None:
        register_dashboard(self)

    def cog_unload(self) -> None:
        unregister_dashboard(self)
        for sess in self._active.values():
            sess["event"].set()
        self._active.clear()

    @staticmethod
    def _t(lang: str, de: str, en: str) -> str:
        return de if str(lang).lower().startswith("de") else en

    async def _lang(self, guild) -> str:
        if guild is None:
            return "en-US"
        return await self.config.guild(guild).language()

    # ------------------------------------------------------------------ #
    # Answer detection
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        sess = self._active.get(message.channel.id)
        if not sess or sess.get("winner"):
            return
        if message.content.strip().lower() in sess["answers"]:
            sess["winner"] = message.author
            sess["event"].set()

    # ------------------------------------------------------------------ #
    # Quiz
    # ------------------------------------------------------------------ #
    @commands.hybrid_group(name="quiz", aliases=["triviagame"])
    @commands.guild_only()
    async def quiz(self, ctx: commands.Context) -> None:
        """Play a quiz / trivia game."""

    @quiz.command(name="start")
    @app_commands.describe(rounds="How many questions (1–20)")
    async def quiz_start(self, ctx: commands.Context, rounds: int = 5) -> None:
        """Start a quiz in this channel."""
        lang = await self._lang(ctx.guild)
        if not await self.config.guild(ctx.guild).enabled():
            await ctx.send(self._t(lang, "Quiz-Modul ist deaktiviert.", "Quiz module is disabled."))
            return
        if ctx.channel.id in self._active:
            await ctx.send(self._t(lang, "Hier läuft schon ein Quiz.", "A quiz is already running here."))
            return
        pool = await self.config.guild(ctx.guild).questions()
        if not pool:
            await ctx.send(self._t(lang, "Keine Fragen hinterlegt.", "No questions configured."))
            return
        rounds = max(1, min(20, rounds, len(pool)))
        questions = random.sample(pool, rounds)
        await ctx.send(self._t(lang, f"🧠 Quiz startet — {rounds} Fragen! Antworte einfach im Chat.",
                               f"🧠 Quiz starting — {rounds} questions! Just answer in chat."))
        round_scores: Dict[int, int] = {}
        for i, q in enumerate(questions, start=1):
            answers = {str(q.get("a", "")).strip().lower()}
            answers |= {str(x).strip().lower() for x in (q.get("alts") or [])}
            answers.discard("")
            ev = asyncio.Event()
            self._active[ctx.channel.id] = {"answers": answers, "winner": None, "event": ev}
            await ctx.send(embed=discord.Embed(
                title=self._t(lang, f"Frage {i}/{rounds}", f"Question {i}/{rounds}"),
                description=str(q.get("q", "")),
                colour=discord.Colour.blurple(),
            ))
            try:
                await asyncio.wait_for(ev.wait(), timeout=ANSWER_TIME)
            except asyncio.TimeoutError:
                pass
            sess = self._active.pop(ctx.channel.id, None)
            winner = sess.get("winner") if sess else None
            if winner is not None:
                round_scores[winner.id] = round_scores.get(winner.id, 0) + 1
                await self.config.member(winner).points.set((await self.config.member(winner).points()) + 1)
                await ctx.send(self._t(lang, f"✅ {winner.mention} richtig! Antwort: **{q.get('a')}**",
                                       f"✅ {winner.mention} got it! Answer: **{q.get('a')}**"))
            else:
                await ctx.send(self._t(lang, f"⏰ Zeit um! Antwort: **{q.get('a')}**", f"⏰ Time! Answer: **{q.get('a')}**"))
            await asyncio.sleep(2)
        # Summary
        if round_scores:
            ranking = sorted(round_scores.items(), key=lambda kv: kv[1], reverse=True)
            lines = []
            for mid, pts in ranking:
                m = ctx.guild.get_member(mid)
                lines.append(f"**{m.display_name if m else mid}** — {pts}")
            await ctx.send(embed=discord.Embed(
                title=self._t(lang, "🏆 Ergebnis", "🏆 Results"),
                description="\n".join(lines),
                colour=discord.Colour.gold(),
            ))
        else:
            await ctx.send(self._t(lang, "Keine richtigen Antworten. 😴", "No correct answers. 😴"))

    @quiz.command(name="stop")
    @commands.admin_or_permissions(manage_messages=True)
    async def quiz_stop(self, ctx: commands.Context) -> None:
        """Stop the running quiz in this channel."""
        lang = await self._lang(ctx.guild)
        sess = self._active.pop(ctx.channel.id, None)
        if sess:
            sess["event"].set()
            await ctx.send(self._t(lang, "Quiz gestoppt.", "Quiz stopped."))
        else:
            await ctx.send(self._t(lang, "Hier läuft kein Quiz.", "No quiz running here."))

    @quiz.command(name="leaderboard", aliases=["top"])
    async def quiz_leaderboard(self, ctx: commands.Context) -> None:
        """Show the all-time quiz leaderboard."""
        lang = await self._lang(ctx.guild)
        members = await self.config.all_members(ctx.guild)
        ranking = sorted(members.items(), key=lambda kv: kv[1].get("points", 0), reverse=True)
        ranking = [(mid, mc) for mid, mc in ranking if mc.get("points", 0) > 0][:10]
        if not ranking:
            await ctx.send(self._t(lang, "Noch keine Punkte.", "No points yet."))
            return
        lines = []
        for i, (mid, mc) in enumerate(ranking, start=1):
            m = ctx.guild.get_member(mid)
            lines.append(f"**{i}.** {m.display_name if m else mid} — {mc.get('points', 0)}")
        await ctx.send(embed=discord.Embed(
            title=self._t(lang, "🏆 Quiz-Bestenliste", "🏆 Quiz leaderboard"),
            description="\n".join(lines),
            colour=discord.Colour.gold(),
        ))

    # ------------------------------------------------------------------ #
    # Management
    # ------------------------------------------------------------------ #
    @commands.hybrid_group(name="quizset")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def quizset(self, ctx: commands.Context) -> None:
        """Configure the quiz module."""

    @quizset.command(name="enable")
    @app_commands.describe(on_off="Enable or disable the quiz")
    async def q_enable(self, ctx: commands.Context, on_off: bool) -> None:
        """Enable/disable the module for this server."""
        lang = await self._lang(ctx.guild)
        await self.config.guild(ctx.guild).enabled.set(on_off)
        state = self._t(lang, "aktiviert" if on_off else "deaktiviert", "enabled" if on_off else "disabled")
        await ctx.send(self._t(lang, f"Quiz **{state}**.", f"Quiz **{state}**."))

    @quizset.command(name="add")
    @app_commands.describe(question="The question", answer="The correct answer")
    async def q_add(self, ctx: commands.Context, question: str, *, answer: str) -> None:
        """Add a question (separate alt answers with `|` in the answer)."""
        lang = await self._lang(ctx.guild)
        parts = [a.strip() for a in answer.split("|") if a.strip()]
        if not parts:
            await ctx.send(self._t(lang, "Antwort fehlt.", "Answer is empty."))
            return
        async with self.config.guild(ctx.guild).questions() as qs:
            qs.append({"q": question.strip(), "a": parts[0], "alts": parts[1:]})
        await ctx.send(self._t(lang, "Frage hinzugefügt.", "Question added."))

    @quizset.command(name="list")
    async def q_list(self, ctx: commands.Context) -> None:
        """List the questions."""
        lang = await self._lang(ctx.guild)
        qs = await self.config.guild(ctx.guild).questions()
        if not qs:
            await ctx.send(self._t(lang, "Keine Fragen.", "No questions."))
            return
        body = "\n".join(f"**{i}.** {q.get('q')} — `{q.get('a')}`" for i, q in enumerate(qs, start=1))
        await ctx.send(embed=discord.Embed(title=self._t(lang, "Fragen", "Questions"), description=body[:4000], colour=await ctx.embed_colour()))

    @quizset.command(name="remove")
    @app_commands.describe(index="Question number from 'quizset list'")
    async def q_remove(self, ctx: commands.Context, index: int) -> None:
        """Remove a question by its number."""
        lang = await self._lang(ctx.guild)
        async with self.config.guild(ctx.guild).questions() as qs:
            ok = 1 <= index <= len(qs)
            if ok:
                qs.pop(index - 1)
        await ctx.send(self._t(lang, "Entfernt." if ok else "Ungültige Nummer.", "Removed." if ok else "Invalid number."))

    @quizset.command(name="language")
    @app_commands.describe(language="Output language: de-DE or en-US")
    async def q_language(self, ctx: commands.Context, language: str) -> None:
        """Set the output language for this server."""
        language = "de-DE" if language.lower().startswith("de") else "en-US"
        await self.config.guild(ctx.guild).language.set(language)
        await ctx.send(self._t(language, "Sprache: Deutsch", "Language: English"))

    # ------------------------------------------------------------------ #
    # Dashboard panel + question table
    # ------------------------------------------------------------------ #
    @dashboard_panel("triviagame", L("Quiz", "Quiz"), mount="guild_settings", permission="guild_admin", order=90)
    async def settings_panel(self, ctx):
        conf = self.config.guild(ctx.guild)
        lang = await conf.language()
        qcount = len(await conf.questions())
        return PanelSchema(
            description=tr_lang(
                lang,
                f"Quiz-Spiel ('quiz start'). {qcount} Fragen. Fragen im Tab 'Fragen' verwalten.",
                f"Quiz game ('quiz start'). {qcount} questions. Manage them in the 'Questions' tab.",
            ),
            fields=[
                Field.switch("enabled", L("Aktiviert", "Enabled"), value=bool(await conf.enabled())),
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
        lang = str(data.get("language", "en-US")).strip() or "en-US"
        await conf.language.set(lang)
        return SubmitResult.ok(tr_lang(lang, "Gespeichert.", "Saved."))

    @dashboard_list(
        "questions", L("Fragen", "Questions"), mount="guild_settings", permission="guild_admin", order=92,
        columns=[{"key": "q", "label": "Question"}, {"key": "a", "label": "Answer"}],
        description=L("Frage → Antwort. Alternativantworten mit | trennen. Neue im Tab 'Frage anlegen'.",
                      "Question → answer. Separate alternatives with |. Add new ones in the 'Add question' tab."),
    )
    async def questions_list(self, ctx):
        qs = await self.config.guild(ctx.guild).questions()
        rows = []
        for i, q in enumerate(qs):
            a = q.get("a", "")
            if q.get("alts"):
                a = " | ".join([a, *q.get("alts")])
            rows.append({"id": str(i), "cells": {"q": str(q.get("q", ""))[:80], "a": str(a)[:60]}})
        return rows

    @questions_list.edit_form
    async def questions_edit_form(self, ctx, item_id):
        qs = await self.config.guild(ctx.guild).questions()
        try:
            q = qs[int(item_id)]
        except (ValueError, IndexError):
            q = {}
        a = q.get("a", "")
        if q.get("alts"):
            a = " | ".join([a, *q.get("alts")])
        return PanelSchema(fields=[
            Field.textarea("q", L("Frage", "Question"), value=str(q.get("q", ""))),
            Field.text("a", L("Antwort(en) — mit | trennen", "Answer(s) — separate with |"), value=str(a)),
        ])

    @questions_list.on_edit
    async def questions_edit(self, ctx, item_id, data):
        lang = await self.config.guild(ctx.guild).language()
        parts = [p.strip() for p in str(data.get("a") or "").split("|") if p.strip()]
        async with self.config.guild(ctx.guild).questions() as qs:
            try:
                idx = int(item_id)
                qs[idx] = {"q": str(data.get("q") or "").strip(), "a": parts[0] if parts else "", "alts": parts[1:]}
            except (ValueError, IndexError):
                return SubmitResult.fail(tr_lang(lang, "Nicht gefunden.", "Not found."))
        return SubmitResult.ok(tr_lang(lang, "Frage gespeichert.", "Question saved."))

    @questions_list.on_delete
    async def questions_delete(self, ctx, item_id):
        lang = await self.config.guild(ctx.guild).language()
        async with self.config.guild(ctx.guild).questions() as qs:
            try:
                qs.pop(int(item_id))
            except (ValueError, IndexError):
                return SubmitResult.fail(tr_lang(lang, "Nicht gefunden.", "Not found."))
        return SubmitResult.ok(tr_lang(lang, "Frage gelöscht.", "Question deleted."), reload=True)

    @dashboard_panel("question_add", L("Frage anlegen", "Add question"), mount="guild_settings", permission="guild_admin", order=91)
    async def question_add_panel(self, ctx):
        lang = await self.config.guild(ctx.guild).language()
        return PanelSchema(
            description=tr_lang(lang, "Neue Frage anlegen. Alternativantworten mit | trennen.", "Add a new question. Separate alternatives with |."),
            fields=[
                Field.textarea("q", L("Frage", "Question"), value=""),
                Field.text("a", L("Antwort(en) — mit | trennen", "Answer(s) — separate with |"), value=""),
            ],
        )

    @question_add_panel.on_submit
    async def _question_add(self, ctx, data):
        lang = await self.config.guild(ctx.guild).language()
        q = str(data.get("q") or "").strip()
        parts = [p.strip() for p in str(data.get("a") or "").split("|") if p.strip()]
        if not q or not parts:
            return SubmitResult.fail(tr_lang(lang, "Frage und Antwort erforderlich.", "Question and answer required."))
        async with self.config.guild(ctx.guild).questions() as qs:
            qs.append({"q": q, "a": parts[0], "alts": parts[1:]})
        return SubmitResult.ok(tr_lang(lang, "Frage angelegt.", "Question added."), reload=True)

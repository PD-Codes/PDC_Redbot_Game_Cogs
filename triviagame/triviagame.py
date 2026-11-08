"""TriviaGame — a quiz game with its own question DB and a leaderboard.

Command group is ``quiz`` (not ``trivia``) to avoid clashing with Red's core
Trivia cog. Questions live in a per-guild database editable from the web
dashboard (a table with add/edit/delete). Opt-in per guild, bilingual (DE/EN).
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import random
import uuid
from typing import Dict, List

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
MAX_TEXT_LEN = 1000  # max characters for a question / answer string

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
            sess["stopped"] = True
            sess["event"].set()
        self._active.clear()

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        """Delete stored points for the given user in all guilds."""
        for guild_id in (await self.config.all_members()).keys():
            await self.config.member_from_ids(guild_id, user_id).clear()

    async def red_get_data_for_user(self, *, user_id: int) -> dict:
        """Return stored points for the given user (per guild) as a file."""
        data = {}
        all_members = await self.config.all_members()
        for guild_id, members in all_members.items():
            if user_id in members:
                data[str(guild_id)] = members[user_id]
        return {"triviagame.json": io.BytesIO(json.dumps(data, default=str).encode("utf-8"))}

    # ------------------------------------------------------------------ #
    # Question helpers (stable IDs)
    # ------------------------------------------------------------------ #
    async def _questions(self, guild) -> List[dict]:
        """Return the guild's questions; legacy entries without an ID get one."""
        conf = self.config.guild(guild).questions
        qs = await conf()
        if any(not q.get("id") for q in qs):
            async with conf() as stored:
                for q in stored:
                    if not q.get("id"):
                        q["id"] = uuid.uuid4().hex
            qs = await conf()
        return qs

    @staticmethod
    def _too_long(question: str, answer: str) -> bool:
        return len(question) > MAX_TEXT_LEN or len(answer) > MAX_TEXT_LEN

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
        if await self.bot.cog_disabled_in_guild(self, message.guild):
            return
        if not await self.bot.ignored_channel_or_guild(message):
            return
        if not await self.bot.allowed_by_whitelist_blacklist(message.author):
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
        # Claim the channel immediately (before the first await) so two
        # parallel starts cannot both pass the check above.
        sess = {"answers": set(), "winner": None, "event": asyncio.Event(), "stopped": False}
        self._active[ctx.channel.id] = sess
        try:
            pool = await self._questions(ctx.guild)
            if not pool:
                await ctx.send(self._t(lang, "Keine Fragen hinterlegt.", "No questions configured."))
                return
            rounds = max(1, min(20, rounds, len(pool)))
            questions = random.sample(pool, rounds)
            await ctx.send(self._t(lang, f"🧠 Quiz startet — {rounds} Fragen! Antworte einfach im Chat.",
                                   f"🧠 Quiz starting — {rounds} questions! Just answer in chat."))
            round_scores: Dict[int, int] = {}
            stopped = False
            for i, q in enumerate(questions, start=1):
                answers = {str(q.get("a", "")).strip().lower()}
                answers |= {str(x).strip().lower() for x in (q.get("alts") or [])}
                answers.discard("")
                sess["answers"] = answers
                sess["winner"] = None
                sess["event"] = asyncio.Event()
                await ctx.send(embed=discord.Embed(
                    title=self._t(lang, f"Frage {i}/{rounds}", f"Question {i}/{rounds}"),
                    description=str(q.get("q", "")),
                    colour=discord.Colour.blurple(),
                ))
                try:
                    await asyncio.wait_for(sess["event"].wait(), timeout=ANSWER_TIME)
                except asyncio.TimeoutError:
                    pass
                # Check the stop flag after each question cycle.
                if sess.get("stopped"):
                    stopped = True
                    await ctx.send(self._t(lang, "🛑 Quiz abgebrochen.", "🛑 Quiz stopped."))
                    break
                winner = sess.get("winner")
                if winner is not None:
                    round_scores[winner.id] = round_scores.get(winner.id, 0) + 1
                    points = self.config.member(winner).points
                    async with points.get_lock():
                        await points.set((await points()) + 1)
                    await ctx.send(self._t(lang, f"✅ {winner.mention} richtig! Antwort: **{q.get('a')}**",
                                           f"✅ {winner.mention} got it! Answer: **{q.get('a')}**"))
                else:
                    await ctx.send(self._t(lang, f"⏰ Zeit um! Antwort: **{q.get('a')}**", f"⏰ Time! Answer: **{q.get('a')}**"))
                await asyncio.sleep(2)
                if sess.get("stopped"):
                    stopped = True
                    await ctx.send(self._t(lang, "🛑 Quiz abgebrochen.", "🛑 Quiz stopped."))
                    break
            # Summary
            if stopped:
                return
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
        finally:
            # Always release the channel, even if the quiz errored out.
            self._active.pop(ctx.channel.id, None)

    @quiz.command(name="stop")
    @commands.admin_or_permissions(manage_messages=True)
    async def quiz_stop(self, ctx: commands.Context) -> None:
        """Stop the running quiz in this channel."""
        lang = await self._lang(ctx.guild)
        sess = self._active.get(ctx.channel.id)
        if sess:
            # Signal the running quiz loop to stop; it cleans itself up.
            sess["stopped"] = True
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
        if self._too_long(question.strip(), answer):
            await ctx.send(self._t(lang, f"Frage/Antwort zu lang (max. {MAX_TEXT_LEN} Zeichen).", f"Question/answer too long (max {MAX_TEXT_LEN} characters)."))
            return
        await self._questions(ctx.guild)
        async with self.config.guild(ctx.guild).questions() as qs:
            qs.append({"id": uuid.uuid4().hex, "q": question.strip(), "a": parts[0], "alts": parts[1:]})
        await ctx.send(self._t(lang, "Frage hinzugefügt.", "Question added."))

    @quizset.command(name="list")
    async def q_list(self, ctx: commands.Context) -> None:
        """List the questions."""
        lang = await self._lang(ctx.guild)
        qs = await self._questions(ctx.guild)
        if not qs:
            await ctx.send(self._t(lang, "Keine Fragen.", "No questions."))
            return
        body = "\n".join(f"**{i}.** `{str(q.get('id', ''))[:8]}` {q.get('q')} — `{q.get('a')}`" for i, q in enumerate(qs, start=1))
        await ctx.send(embed=discord.Embed(title=self._t(lang, "Fragen", "Questions"), description=body[:4000], colour=await ctx.embed_colour()))

    @quizset.command(name="remove")
    @app_commands.describe(ref="Question ID (8 characters) or number from 'quizset list'")
    async def q_remove(self, ctx: commands.Context, ref: str) -> None:
        """Remove a question by its ID (preferred) or its list number."""
        lang = await self._lang(ctx.guild)
        ref = ref.strip().lower()
        await self._questions(ctx.guild)
        ok = False
        async with self.config.guild(ctx.guild).questions() as qs:
            idx = None
            if len(ref) == 8:
                matches = [i for i, q in enumerate(qs) if str(q.get("id", "")).startswith(ref)]
                if len(matches) == 1:
                    idx = matches[0]
            if idx is None and ref.isdigit() and 1 <= int(ref) <= len(qs):
                idx = int(ref) - 1
            if idx is not None:
                qs.pop(idx)
                ok = True
        await ctx.send(self._t(lang, "Entfernt." if ok else "Ungültige ID/Nummer.", "Removed." if ok else "Invalid ID/number."))

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
        qs = await self._questions(ctx.guild)
        rows = []
        for q in qs:
            a = q.get("a", "")
            if q.get("alts"):
                a = " | ".join([a, *q.get("alts")])
            rows.append({"id": str(q["id"]), "cells": {"q": str(q.get("q", ""))[:80], "a": str(a)[:60]}})
        return rows

    @questions_list.edit_form
    async def questions_edit_form(self, ctx, item_id):
        qs = await self._questions(ctx.guild)
        q = next((x for x in qs if str(x.get("id")) == str(item_id)), {})
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
        question = str(data.get("q") or "").strip()
        raw = str(data.get("a") or "")
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        if not question or not parts:
            return SubmitResult.fail(tr_lang(lang, "Frage und Antwort erforderlich.", "Question and answer required."))
        if self._too_long(question, raw):
            return SubmitResult.fail(tr_lang(lang, f"Zu lang (max. {MAX_TEXT_LEN} Zeichen).", f"Too long (max {MAX_TEXT_LEN} characters)."))
        await self._questions(ctx.guild)
        async with self.config.guild(ctx.guild).questions() as qs:
            for q in qs:
                if str(q.get("id")) == str(item_id):
                    q["q"], q["a"], q["alts"] = question, parts[0], parts[1:]
                    break
            else:
                return SubmitResult.fail(tr_lang(lang, "Nicht gefunden.", "Not found."))
        return SubmitResult.ok(tr_lang(lang, "Frage gespeichert.", "Question saved."))

    @questions_list.on_delete
    async def questions_delete(self, ctx, item_id):
        lang = await self.config.guild(ctx.guild).language()
        await self._questions(ctx.guild)
        async with self.config.guild(ctx.guild).questions() as qs:
            for i, q in enumerate(qs):
                if str(q.get("id")) == str(item_id):
                    qs.pop(i)
                    break
            else:
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
        raw = str(data.get("a") or "")
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        if not q or not parts:
            return SubmitResult.fail(tr_lang(lang, "Frage und Antwort erforderlich.", "Question and answer required."))
        if self._too_long(q, raw):
            return SubmitResult.fail(tr_lang(lang, f"Zu lang (max. {MAX_TEXT_LEN} Zeichen).", f"Too long (max {MAX_TEXT_LEN} characters)."))
        await self._questions(ctx.guild)
        async with self.config.guild(ctx.guild).questions() as qs:
            qs.append({"id": uuid.uuid4().hex, "q": q, "a": parts[0], "alts": parts[1:]})
        return SubmitResult.ok(tr_lang(lang, "Frage angelegt.", "Question added."), reload=True)

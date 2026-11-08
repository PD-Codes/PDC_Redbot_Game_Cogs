"""Shared Blizzard API helpers for World of Warcraft: Forever.

The Forever API is undocumented. Everything here follows community tests
(Nov 2026) and is written defensively:

* Namespaces: ``profile-forever-{region}``, ``dynamic-forever-{region}`` and
  ``static-forever-{region}`` (static falls back to the versioned namespace
  ``static-{FOREVER_STATIC_BUILD}-forever-{region}`` on HTTP 403).
* No realms: characters are addressed by ``{name}-{surname}`` (lowercase),
  guilds by their guild slug only.
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote

import aiohttp

from .autocomplete import _API_HOST, _AUTH_HOST, FOREVER_STATIC_BUILD
from .exceptions import InvalidBlizzardAPI
from .pdc_dashboard import tr_lang

WOWHEAD_BASE = "https://www.wowhead.com/forever"
RENDER_BASE = "https://render.worldofwarcraft.com/forever-{region}"

# Regions where the plain static namespace answered 403 and the versioned one worked.
_STATIC_PREFER_VERSIONED: Dict[str, bool] = {}


class ForeverAPIError(RuntimeError):
    """Non-200 answer from the Blizzard API."""

    def __init__(self, status: int, payload: Any = None, path: str = "") -> None:
        self.status = status
        self.payload = payload
        self.path = path
        detail = ""
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("error_description") or payload.get("type") or ""
        super().__init__(f"{status}{': ' + str(detail) if detail else ''}")

    @property
    def unavailable(self) -> bool:
        """403/404 -> endpoint missing in Forever or object not found."""
        return self.status in (403, 404)


# --------------------------------------------------------------------------- #
# Names / slugs
# --------------------------------------------------------------------------- #
def _clean(part: Optional[str]) -> str:
    part = unicodedata.normalize("NFC", str(part or "")).strip()
    return re.sub(r"\s+", " ", part)


def character_slug(name: str, surname: str) -> str:
    """``{name}-{surname}`` in lowercase; inner whitespace becomes a hyphen."""
    raw = f"{_clean(name)}-{_clean(surname)}".lower()
    raw = re.sub(r"[\s_]+", "-", raw)
    return re.sub(r"-{2,}", "-", raw).strip("-")


def guild_slug(guild_name: str) -> str:
    raw = _clean(guild_name).lower()
    raw = re.sub(r"[\s_]+", "-", raw)
    return re.sub(r"-{2,}", "-", raw).strip("-")


def _cap(word: str) -> str:
    return word[:1].upper() + word[1:]


def display_name(name: str, surname: str) -> str:
    """Human readable "Name Surname"."""
    return " ".join(_cap(p) for p in (_clean(name), _clean(surname)) if p)


def member_display_name(character: dict) -> str:
    """Display name for a roster/profile character node (defensive field access)."""
    name = (character or {}).get("name") or ""
    surname = (character or {}).get("surname") or ""
    if isinstance(name, dict):  # localized names, just in case
        name = name.get("en_US") or next(iter(name.values()), "")
    if isinstance(surname, dict):
        surname = surname.get("en_US") or next(iter(surname.values()), "")
    full = f"{name} {surname}".strip()
    return full or str((character or {}).get("slug") or "?")


def wowhead_url(kind: str, obj_id: Any) -> str:
    return f"{WOWHEAD_BASE}/{kind}={obj_id}"


# --------------------------------------------------------------------------- #
# Messages (bilingual, same style as tr_lang elsewhere in this cog)
# --------------------------------------------------------------------------- #
async def get_lang(cog, ctx) -> str:
    guild = getattr(ctx, "guild", None)
    if guild is None:
        return "en-US"
    try:
        return await cog.config.guild(guild).language()
    except Exception:
        return "en-US"


def not_available_msg(lang: str, feature_de: str, feature_en: Optional[str] = None) -> str:
    feature_en = feature_en or feature_de
    return tr_lang(
        lang,
        f"**{feature_de}** ist in WoW Forever nicht verfügbar.",
        f"**{feature_en}** is not available in WoW Forever.",
    )


async def resolve_character_args(cog, ctx, region: Optional[str], name: Optional[str], surname: Optional[str]):
    """Fill missing region/name/surname from the user's saved character settings.

    Values come from ``wowtf-wowset character name/surname/region``; the region
    additionally falls back to the server region. Returns
    ``(region, name, surname, error)`` where ``error`` is a ready-to-send message
    (or None when everything is known).
    """
    lang = await get_lang(cog, ctx)
    try:
        saved = await cog.config.user(ctx.author).all()
    except Exception:
        saved = {}
    s_name = str(saved.get("wow_character_name") or "").strip()
    s_surname = str(saved.get("wow_character_surname") or "").strip()
    s_region = str(saved.get("wow_character_region") or "").strip().lower()

    name = (name or "").strip()
    surname = (surname or "").strip()
    if not name:
        name = s_name
        surname = surname or s_surname
    elif not surname and s_surname and name.lower() == s_name.lower():
        # Only reuse the saved surname for the saved character.
        surname = s_surname

    region = (region or "").strip().lower()
    if not region:
        region = s_region
    if not region and getattr(ctx, "guild", None) is not None:
        try:
            region = str(await cog.config.guild(ctx.guild).region() or "").lower()
        except Exception:
            region = ""

    if not name or not surname or not region:
        return region, name, surname, tr_lang(
            lang,
            "Bitte Region, Vorname und Nachname angeben oder deinen Charakter mit "
            "`[p]wowtf-wowset character name|surname|region` speichern.",
            "Please provide region, first name and surname, or save your character with "
            "`[p]wowtf-wowset character name|surname|region`.",
        )
    return region, name, surname, None


def character_not_found_msg(lang: str, display: str) -> str:
    return tr_lang(
        lang,
        f"Charakter **{display}** wurde nicht gefunden.\n"
        "Hinweis: In WoW Forever erscheinen Charaktere erst ab Level 10 und nachdem sie "
        "sich ausgeloggt haben. Name und Nachname bitte genau wie im Spiel angeben.",
        f"Character **{display}** was not found.\n"
        "Note: In WoW Forever, characters only appear from level 10 and after logging out. "
        "Please enter first name and surname exactly as in-game.",
    )


async def explain_character_error(
    cog,
    lang: str,
    err: Exception,
    *,
    region: str,
    name: str,
    surname: str,
    feature_de: str,
    feature_en: str,
    prefix_de: str,
    prefix_en: str,
) -> str:
    """Turn an API error of a character sub route into a friendly message.

    A 403/404 on a sub route can mean "character unknown" or "endpoint does not
    exist in Forever". The profile summary is checked to tell both apart.
    """
    disp = display_name(name, surname)
    if isinstance(err, ForeverAPIError) and err.unavailable:
        try:
            await fetch_character(cog, region, name, surname)
        except ForeverAPIError as e2:
            if e2.status == 404:
                return character_not_found_msg(lang, disp)
        except Exception:
            pass
        else:
            return not_available_msg(lang, feature_de, feature_en)
        if err.status == 404:
            return character_not_found_msg(lang, disp)
        return not_available_msg(lang, feature_de, feature_en)
    return tr_lang(lang, f"{prefix_de}: {err}", f"{prefix_en}: {err}")


# --------------------------------------------------------------------------- #
# OAuth (client credentials, shared API tokens "blizzard")
# --------------------------------------------------------------------------- #
def _ensure_oauth_state(cog) -> None:
    if not hasattr(cog, "_forever_tok_lock"):
        cog._forever_tok_lock = asyncio.Lock()
    if not hasattr(cog, "_forever_tok"):
        cog._forever_tok = {}
    if not hasattr(cog, "_forever_exp"):
        cog._forever_exp = {}


def reset_token_cache(cog) -> None:
    _ensure_oauth_state(cog)
    cog._forever_tok.clear()
    cog._forever_exp.clear()


async def get_access_token(cog, region: str) -> str:
    _ensure_oauth_state(cog)
    region = (region or "eu").lower()
    async with cog._forever_tok_lock:
        now = datetime.now(timezone.utc)
        tok = cog._forever_tok.get(region)
        exp = cog._forever_exp.get(region)
        if tok and exp and now < exp:
            return tok
        api_tokens = await cog.bot.get_shared_api_tokens("blizzard")
        cid, secret = api_tokens.get("client_id"), api_tokens.get("client_secret")
        if not cid or not secret:
            raise InvalidBlizzardAPI(
                "Blizzard API not set up / nicht eingerichtet: "
                "`[p]set api blizzard client_id,<id> client_secret,<secret>`"
            )
        auth_host = _AUTH_HOST.get(region, "eu.battle.net")
        url = f"https://{auth_host}/oauth/token"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, data={"grant_type": "client_credentials"}, auth=aiohttp.BasicAuth(cid, secret)
            ) as resp:
                js = await _read_json(resp)
                if resp.status != 200:
                    raise RuntimeError(f"Auth {resp.status}: {js}")
        token = js["access_token"]
        expires_in = int(js.get("expires_in", 3600))
        cog._forever_tok[region] = token
        cog._forever_exp[region] = now + timedelta(seconds=max(30, expires_in - 30))
        return token


# --------------------------------------------------------------------------- #
# Generic GET
# --------------------------------------------------------------------------- #
def namespace(kind: str, region: str, versioned: bool = False) -> str:
    region = (region or "eu").lower()
    if kind == "static" and versioned:
        return f"static-{FOREVER_STATIC_BUILD}-forever-{region}"
    return f"{kind}-forever-{region}"


async def _read_json(resp: aiohttp.ClientResponse) -> Any:
    try:
        return await resp.json(content_type=None)
    except Exception:
        try:
            return {"detail": (await resp.text())[:200]}
        except Exception:
            return {}


async def api_get(
    cog,
    region: str,
    path: str,
    *,
    kind: str = "profile",
    locale: str = "en_US",
    params: Optional[dict] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> dict:
    """GET ``path`` (must start with "/") with the Forever namespace of ``kind``.

    ``kind`` is "profile", "dynamic" or "static". Static requests try the plain
    namespace first and retry once with the versioned namespace on HTTP 403.
    Raises :class:`ForeverAPIError` on any non-200 answer.
    """
    region = (region or "eu").lower()
    host = _API_HOST.get(region, "eu.api.blizzard.com")
    token = await get_access_token(cog, region)
    headers = {"Authorization": f"Bearer {token}"}

    attempts = [False]
    if kind == "static":
        attempts = [True, False] if _STATIC_PREFER_VERSIONED.get(region) else [False, True]

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        last_err: Optional[ForeverAPIError] = None
        for idx, versioned in enumerate(attempts):
            query = {"namespace": namespace(kind, region, versioned), "locale": locale}
            if params:
                query.update(params)
            async with session.get(f"https://{host}{path}", params=query, headers=headers) as resp:
                js = await _read_json(resp)
                if resp.status == 200:
                    if kind == "static":
                        _STATIC_PREFER_VERSIONED[region] = versioned
                    return js if isinstance(js, dict) else {"data": js}
                last_err = ForeverAPIError(resp.status, js, path)
                if resp.status == 403 and idx + 1 < len(attempts):
                    continue
                raise last_err
        raise last_err or ForeverAPIError(0, None, path)
    finally:
        if own_session:
            await session.close()


async def fetch_character(
    cog, region: str, name: str, surname: str, sub: str = "", *, locale: str = "en_US"
) -> dict:
    """``/profile/wow/character/{name}-{surname}[/{sub}]`` in the profile namespace."""
    slug = quote(character_slug(name, surname), safe="-")
    path = f"/profile/wow/character/{slug}" + (f"/{sub.strip('/')}" if sub else "")
    return await api_get(cog, region, path, kind="profile", locale=locale)


async def fetch_guild(cog, region: str, guild_name: str, sub: str = "", *, locale: str = "en_US") -> dict:
    """``/data/wow/guild/{guild-slug}[/{sub}]`` (realmless) in the profile namespace."""
    slug = quote(guild_slug(guild_name), safe="-")
    path = f"/data/wow/guild/{slug}" + (f"/{sub.strip('/')}" if sub else "")
    return await api_get(cog, region, path, kind="profile", locale=locale)


async def fetch_item_levels(
    cog, region: str, item_ids, *, locale: str = "en_US", concurrency: int = 5
) -> Dict[int, Optional[int]]:
    """Item level per item id from ``/data/wow/item/{id}`` (static namespace)."""
    uniq_ids = list({i for i in item_ids if i})
    if not uniq_ids:
        return {}
    sem = asyncio.Semaphore(concurrency)
    results: Dict[int, Optional[int]] = {}

    async def fetch_one(session: aiohttp.ClientSession, iid: int) -> None:
        async with sem:
            try:
                js = await api_get(cog, region, f"/data/wow/item/{iid}", kind="static", locale=locale, session=session)
                results[iid] = js.get("level")
            except Exception:
                results[iid] = None

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*(fetch_one(session, iid) for iid in uniq_ids))
    return results

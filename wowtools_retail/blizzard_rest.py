"""Shared Blizzard REST helpers for this cog (namespace-aware, cached OAuth token).

aiowowapi 2.1.5 has no ``Classic.Profile`` and no spell search in
``Classic.GameData``, so the affected features (PvP rating, guild roster,
[[...]] auto-reply) call the Blizzard REST API directly. The namespace follows
``GAME`` from ``autocomplete``:

* retail:  ``profile-{region}``, ``static-{region}``, ``dynamic-{region}``
* classic: ``profile-classic-{region}``, ``static-classic-{region}``,
  ``dynamic-classic-{region}``

Endpoints that do not exist in a namespace answer 403/404; callers check
:attr:`BlizzardAPIError.unavailable` and show a "not available" message.
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

from .autocomplete import GAME, _API_HOST, _AUTH_HOST, cached_realm_slug
from .exceptions import InvalidBlizzardAPI

# Every per-module OAuth token cache that lives on the cog instance. They are all
# cleared when the "blizzard" shared API tokens change or the API answers 401.
_TOKEN_CACHE_ATTRS = (
    "_tok",
    "_exp",
    "_gear_tok",
    "_gear_exp",
    "_wowtoken_tok",
    "_wowtoken_exp",
)


class BlizzardAPIError(RuntimeError):
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
        """403/404 -> endpoint missing in this namespace or object not found."""
        return self.status in (403, 404)


def reset_token_caches(cog) -> None:
    """Drop all cached Blizzard OAuth tokens of ``cog``."""
    for attr in _TOKEN_CACHE_ATTRS:
        cache = getattr(cog, attr, None)
        if isinstance(cache, dict):
            cache.clear()


# --------------------------------------------------------------------------- #
# Slugs / URL path segments
# --------------------------------------------------------------------------- #
def slugify(name: Optional[str]) -> str:
    """Blizzard-style slug for realm (and guild) names.

    Lowercase, apostrophes/quotes and brackets removed, whitespace -> "-",
    accents are kept (e.g. "Aggra (Português)" -> "aggra-português",
    "Kel'Thuzad" -> "kelthuzad", "Twisting Nether" -> "twisting-nether").
    Hyphens are kept because users often type them instead of spaces; names
    whose real slug differs (e.g. "Azjol-Nerub" -> "azjolnerub", Cyrillic
    realms) are resolved through the cached realm index instead.
    """
    s = unicodedata.normalize("NFC", str(name or "")).strip().casefold()
    s = re.sub(r"[\'\"`´’‘]", "", s)
    s = re.sub(r"[()\[\]{}.,:;!?]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-")


def path_segment(value: Any) -> str:
    """Percent-encode a single URL path segment (also encodes "/")."""
    return quote(str(value), safe="")


def realm_slug(realm: Optional[str], region: Optional[str] = None) -> str:
    """Realm slug: exact match from the cached realm index, else :func:`slugify`."""
    return cached_realm_slug(region, realm or "") or slugify(realm)


def realm_segment(realm: Optional[str], region: Optional[str] = None) -> str:
    return path_segment(realm_slug(realm, region))


def character_segment(name: Optional[str]) -> str:
    """Character names are lowercase in profile URLs; special characters are encoded."""
    return path_segment(unicodedata.normalize("NFC", str(name or "")).strip().lower())


# --------------------------------------------------------------------------- #
# OAuth (client credentials) - same cache attributes as charinfo.py & co.
# --------------------------------------------------------------------------- #
def _ensure_oauth_state(cog) -> None:
    if not hasattr(cog, "_tok_lock"):
        cog._tok_lock = asyncio.Lock()
    if not hasattr(cog, "_tok"):
        cog._tok = {}
    if not hasattr(cog, "_exp"):
        cog._exp = {}


async def _read_json(resp: aiohttp.ClientResponse) -> Any:
    try:
        return await resp.json(content_type=None)
    except Exception:
        try:
            return {"detail": (await resp.text())[:200]}
        except Exception:
            return {}


async def get_access_token(cog, region: str) -> str:
    _ensure_oauth_state(cog)
    region = (region or "eu").lower()
    async with cog._tok_lock:
        now = datetime.now(timezone.utc)
        tok = cog._tok.get(region)
        exp = cog._exp.get(region)
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
        cog._tok[region] = token
        cog._exp[region] = now + timedelta(seconds=max(30, expires_in - 30))
        return token


# --------------------------------------------------------------------------- #
# Generic GET
# --------------------------------------------------------------------------- #
def namespace(kind: str, region: str) -> str:
    """``kind`` is "profile", "static" or "dynamic"."""
    region = (region or "eu").lower()
    if GAME == "classic":
        return f"{kind}-classic-{region}"
    return f"{kind}-{region}"


async def api_get(
    cog,
    region: str,
    path: str,
    *,
    kind: str = "profile",
    locale: Optional[str] = "en_US",
    params: Optional[dict] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> dict:
    """GET ``path`` (must start with "/") with this cog's namespace of ``kind``.

    Retries once with a fresh token on HTTP 401. Raises :class:`BlizzardAPIError`
    on any other non-200 answer and :class:`InvalidBlizzardAPI` without credentials.
    """
    region = (region or "eu").lower()
    host = _API_HOST.get(region, "eu.api.blizzard.com")
    query: dict = {"namespace": namespace(kind, region)}
    if locale:
        query["locale"] = locale
    if params:
        query.update(params)

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        for attempt in (0, 1):
            token = await get_access_token(cog, region)
            headers = {"Authorization": f"Bearer {token}"}
            async with session.get(f"https://{host}{path}", params=query, headers=headers) as resp:
                js = await _read_json(resp)
                if resp.status == 200:
                    return js if isinstance(js, dict) else {"data": js}
                if resp.status == 401 and attempt == 0:
                    reset_token_caches(cog)
                    continue
                raise BlizzardAPIError(resp.status, js, path)
        raise BlizzardAPIError(401, None, path)
    finally:
        if own_session:
            await session.close()


async def fetch_character(
    cog,
    region: str,
    realm: str,
    name: str,
    sub: str = "",
    *,
    locale: str = "en_US",
    session: Optional[aiohttp.ClientSession] = None,
) -> dict:
    """``/profile/wow/character/{realm}/{name}[/{sub}]`` in the profile namespace."""
    path = f"/profile/wow/character/{realm_segment(realm, region)}/{character_segment(name)}"
    if sub:
        path += f"/{sub.strip('/')}"
    return await api_get(cog, region, path, kind="profile", locale=locale, session=session)


async def fetch_guild_roster(cog, region: str, realm: str, guild_name: str, *, locale: str = "en_US") -> dict:
    """``/data/wow/guild/{realm}/{guild}/roster`` in the profile namespace."""
    # Assumption: guild slugs follow the same rules as realm slugs.
    path = (
        f"/data/wow/guild/{realm_segment(realm, region)}/"
        f"{path_segment(slugify(guild_name))}/roster"
    )
    return await api_get(cog, region, path, kind="profile", locale=locale)

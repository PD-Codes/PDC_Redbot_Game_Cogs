from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
import asyncio
import base64
import logging
import re
import time

import aiohttp

log = logging.getLogger("red.wowguild_automation.blizzard")

# Network / decode errors that must never escape the service (auto-sync would abort).
_API_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, ValueError)
# Short negative cache per roster key after a failed request (seconds).
ROSTER_ERROR_TTL = 300


@dataclass
class GuildMember:
    character_name: str
    rank_name: str
    rank_index: int


# WoW Forever version keys (kept local so this module stays free of discord/redbot imports).
_FOREVER_VERSIONS = ("forever", "wow_forever", "wf", "wowforever")


def _is_forever(version: str) -> bool:
    return str(version or "").strip().lower().replace("-", "_").replace(" ", "_") in _FOREVER_VERSIONS


def _forever_member_names(char: dict) -> Tuple[str, Set[str]]:
    """
    WoW Forever roster member -> (display "Name Surname", set of lowercase match keys).

    Unofficial, community-tested API shape: character has "name", "surname" and
    "slug" ("name-surname"). All fields are read defensively.
    """
    name = str(char.get("name", "") or "").strip()
    surname = str(char.get("surname", "") or "").strip()
    slug = str(char.get("slug", "") or "").strip().lower()
    if not surname and slug and "-" in slug and name and slug.startswith(name.lower() + "-"):
        # Fallback: derive the surname from the slug when the field is missing.
        surname = slug[len(name) + 1:]
    display = f"{name} {surname}".strip() or slug
    keys: Set[str] = set()
    if slug:
        keys.add(slug)
    if name and surname:
        keys.add(f"{name}-{surname}".lower())
        keys.add(f"{name} {surname}".lower())
    elif name:
        keys.add(name.lower())
    return display, keys


def _forever_target_key(character_name: str) -> str:
    """User input "Name Surname" / "name-surname" -> lowercase "name-surname"."""
    raw = (character_name or "").strip()
    parts = raw.split(None, 1)
    if len(parts) == 2:
        return f"{parts[0]}-{parts[1].strip()}".lower()
    return raw.lower()


class BlizzardService:
    def __init__(self, client_id: str = "", client_secret: str = "") -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._token_cache: Dict[str, Tuple[str, float]] = {}
        self._roster_cache: Dict[str, Tuple[dict, float]] = {}
        self._roster_error_cache: Dict[str, float] = {}

    def _slugify(self, value: str) -> str:
        slug = value.strip().lower().replace("'", "")
        slug = re.sub(r"[^a-z0-9\- ]+", "", slug)
        slug = slug.replace(" ", "-")
        slug = re.sub(r"-{2,}", "-", slug)
        return slug

    def _namespace(self, version: str, region: str) -> str:
        v = (version or "retail").lower()
        r = (region or "eu").lower()
        if v in ("classic", "classic_era", "sod", "mop_classic"):
            return f"profile-classic-{r}"
        if _is_forever(v):
            return f"profile-forever-{r}"
        return f"profile-{r}"

    async def _get_token(self, region: str) -> Optional[str]:
        if not (self.client_id and self.client_secret):
            return None
        region = (region or "eu").lower()
        now = time.time()
        cached = self._token_cache.get(region)
        if cached and cached[1] > now:
            return cached[0]
        token_url = f"https://{region}.battle.net/oauth/token"
        auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("utf-8")
        headers = {"Authorization": f"Basic {auth}"}
        data = {"grant_type": "client_credentials"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(token_url, headers=headers, data=data, timeout=20) as resp:
                    if resp.status != 200:
                        return None
                    payload = await resp.json()
        except _API_ERRORS as exc:
            log.warning("Blizzard token request failed (%s): %r", region, exc)
            return None
        if not isinstance(payload, dict):
            return None
        token = payload.get("access_token")
        try:
            expires_in = int(payload.get("expires_in", 0))
        except (TypeError, ValueError):
            expires_in = 0
        if not token:
            return None
        self._token_cache[region] = (token, now + max(60, expires_in - 60))
        return token

    async def _get_roster(self, region: str, version: str, realm: str, guild_name: str) -> Optional[dict]:
        region = (region or "eu").lower()
        namespace = self._namespace(version, region)
        forever = _is_forever(version)
        # WoW Forever has no realms; realm is ignored there.
        realm_slug = "" if forever else self._slugify(realm or "")
        guild_slug = self._slugify(guild_name)
        cache_key = f"{region}:{namespace}:{realm_slug}:{guild_slug}"
        now = time.time()
        cached = self._roster_cache.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]
        if self._roster_error_cache.get(cache_key, 0) > now:
            # Recent failure for this roster: do not hammer the API.
            return None
        token = await self._get_token(region)
        if not token:
            self._roster_error_cache[cache_key] = now + ROSTER_ERROR_TTL
            return None
        if forever:
            # Realm-less guild roster (unofficial, community-tested endpoint for WoW Forever;
            # a client-credentials token is assumed to be sufficient).
            url = f"https://{region}.api.blizzard.com/data/wow/guild/{guild_slug}/roster"
        else:
            url = f"https://{region}.api.blizzard.com/data/wow/guild/{realm_slug}/{guild_slug}/roster"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"namespace": namespace, "locale": "en_US"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params, timeout=25) as resp:
                    if resp.status != 200:
                        self._roster_error_cache[cache_key] = now + ROSTER_ERROR_TTL
                        return None
                    payload = await resp.json()
        except _API_ERRORS as exc:
            log.warning("Blizzard roster request failed (%s): %r", cache_key, exc)
            self._roster_error_cache[cache_key] = now + ROSTER_ERROR_TTL
            return None
        if not isinstance(payload, dict):
            self._roster_error_cache[cache_key] = now + ROSTER_ERROR_TTL
            return None
        self._roster_error_cache.pop(cache_key, None)
        self._roster_cache[cache_key] = (payload, now + 300)
        return payload

    async def search_member(
        self, region: str, version: str, realm: str, guild_name: str, character_name: str
    ) -> Optional[GuildMember]:
        forever = _is_forever(version)
        if not (region and (realm or forever) and guild_name and character_name):
            return None
        roster = await self._get_roster(region, version, realm, guild_name)
        if not roster:
            return None
        target = character_name.strip().lower()
        if forever:
            # Forever: only "name surname" / slug is unique; the first name alone never matches.
            target_key = _forever_target_key(character_name)
            for entry in roster.get("members", []) or []:
                char = entry.get("character", {}) or {}
                display, keys = _forever_member_names(char)
                if target in keys or target_key in keys:
                    try:
                        rank_index = int(entry.get("rank", 0) or 0)
                    except (TypeError, ValueError):
                        rank_index = 0
                    return GuildMember(
                        character_name=display,
                        rank_name=f"Rank {rank_index}",
                        rank_index=rank_index,
                    )
            return None
        for entry in roster.get("members", []) or []:
            char = entry.get("character", {}) or {}
            char_name = str(char.get("name", "")).strip()
            if char_name.lower() == target:
                try:
                    rank_index = int(entry.get("rank", 0) or 0)
                except (TypeError, ValueError):
                    rank_index = 0
                return GuildMember(
                    character_name=char_name,
                    rank_name=f"Rank {rank_index}",
                    rank_index=rank_index,
                )
        return None

    async def roster_character_names(
        self, region: str, version: str, realm: str, guild_name: str
    ) -> List[str]:
        """
        Sorted unique character names on the guild roster for the given game version.
        WoW Forever: entries are "Name Surname" (unique key is the slug name-surname).
        """
        forever = _is_forever(version)
        if not (region and (realm or forever) and guild_name):
            return []
        roster = await self._get_roster(region, version, realm, guild_name)
        if not roster:
            return []
        names: List[str] = []
        seen: Set[str] = set()
        if forever:
            for entry in roster.get("members", []) or []:
                display, keys = _forever_member_names(entry.get("character", {}) or {})
                if not display:
                    continue
                low = display.lower()
                if low in seen:
                    continue
                seen.add(low)
                names.append(display)
            names.sort(key=str.lower)
            return names
        for entry in roster.get("members", []):
            char = entry.get("character", {})
            name = str(char.get("name", "")).strip()
            if not name:
                continue
            low = name.lower()
            if low in seen:
                continue
            seen.add(low)
            names.append(name)
        names.sort(key=str.lower)
        return names

    async def get_member_characters(
        self, region: str, version: str, realm: str, guild_name: str, character_name: str
    ) -> List[Dict[str, str]]:
        member = await self.search_member(region, version, realm, guild_name, character_name)
        if not member:
            return []
        return [
            {
                "name": member.character_name,
                "realm": realm,
                "rank": member.rank_name,
                "rank_index": str(member.rank_index),
            }
        ]


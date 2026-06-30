from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
import base64
import re
import time

import aiohttp


@dataclass
class GuildMember:
    character_name: str
    rank_name: str
    rank_index: int


class BlizzardService:
    def __init__(self, client_id: str = "", client_secret: str = "") -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._token_cache: Dict[str, Tuple[str, float]] = {}
        self._roster_cache: Dict[str, Tuple[dict, float]] = {}

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
        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, headers=headers, data=data, timeout=20) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json()
        token = payload.get("access_token")
        expires_in = int(payload.get("expires_in", 0))
        if not token:
            return None
        self._token_cache[region] = (token, now + max(60, expires_in - 60))
        return token

    async def _get_roster(self, region: str, version: str, realm: str, guild_name: str) -> Optional[dict]:
        token = await self._get_token(region)
        if not token:
            return None
        region = (region or "eu").lower()
        namespace = self._namespace(version, region)
        realm_slug = self._slugify(realm)
        guild_slug = self._slugify(guild_name)
        cache_key = f"{region}:{namespace}:{realm_slug}:{guild_slug}"
        now = time.time()
        cached = self._roster_cache.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]
        url = f"https://{region}.api.blizzard.com/data/wow/guild/{realm_slug}/{guild_slug}/roster"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"namespace": namespace, "locale": "en_US"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params, timeout=25) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json()
        self._roster_cache[cache_key] = (payload, now + 300)
        return payload

    async def search_member(
        self, region: str, version: str, realm: str, guild_name: str, character_name: str
    ) -> Optional[GuildMember]:
        if not (region and realm and guild_name and character_name):
            return None
        roster = await self._get_roster(region, version, realm, guild_name)
        if not roster:
            return None
        target = character_name.strip().lower()
        for entry in roster.get("members", []):
            char = entry.get("character", {})
            char_name = str(char.get("name", "")).strip()
            if char_name.lower() == target:
                rank_index = int(entry.get("rank", 0))
                return GuildMember(
                    character_name=char_name,
                    rank_name=f"Rank {rank_index}",
                    rank_index=rank_index,
                )
        return None

    async def roster_character_names(
        self, region: str, version: str, realm: str, guild_name: str
    ) -> List[str]:
        """Sorted unique character names on the guild roster for the given game version."""
        if not (region and realm and guild_name):
            return []
        roster = await self._get_roster(region, version, realm, guild_name)
        if not roster:
            return []
        names: List[str] = []
        seen: Set[str] = set()
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


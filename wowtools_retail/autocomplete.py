import time
from typing import Optional

import aiohttp
from discord import app_commands

REGIONS = [
    "eu",
    "us",
    "kr",
    "tw",
    # "CN", # RIP
]

_API_HOST = {
    "eu": "eu.api.blizzard.com",
    "us": "us.api.blizzard.com",
    "kr": "kr.api.blizzard.com",
    "tw": "kr.api.blizzard.com",
}
_AUTH_HOST = {
    "eu": "eu.battle.net",
    "us": "us.battle.net",
    "kr": "apac.battle.net", 
    "tw": "apac.battle.net", 
}

_LANG_CODES = {
    "de": "de_DE",
    "en": "en_US",
    "fr": "fr_FR",
    "es": "es_ES",
    "it": "it_IT",
    "pt": "pt_PT",
    "ru": "ru_RU",
}

# --------------------------------------------------------------------------- #
# Dynamic realm list — retail and classic use different Blizzard namespaces, so
# each cog fetches its own realm list live (cached) instead of a static dict.
# --------------------------------------------------------------------------- #
GAME = "retail"  # wowtools_retail -> "retail" | wowtools_classic -> "classic"

_REALM_CACHE: dict[str, tuple[float, list[dict]]] = {}
_REALM_TTL = 21600  # seconds (6h)


async def fetch_realm_index(get_token, region: str) -> list[dict]:
    """Live realm index for ``region`` using the namespace of this cog's game.

    Returns a list of ``{"name": str, "slug": str}`` sorted by name. ``get_token``
    is an async callable ``(region) -> bearer token``. Returns an empty list on
    any error so the autocomplete never breaks.
    """
    region = (region or "eu").lower()
    if region not in _API_HOST:
        return []
    key = f"{GAME}:{region}"
    now = time.time()
    hit = _REALM_CACHE.get(key)
    if hit and (now - hit[0]) < _REALM_TTL:
        return hit[1]
    host = _API_HOST[region]
    namespace = f"dynamic-{region}" if GAME == "retail" else f"dynamic-classic-{region}"
    realms: list[dict] = []
    try:
        token = await get_token(region)
        url = f"https://{host}/data/wow/realm/index"
        params = {"namespace": namespace, "locale": "en_US"}
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    js = await resp.json()
                    seen: dict[str, str] = {}
                    for r in js.get("realms", []) or []:
                        name, slug = r.get("name"), r.get("slug")
                        # "name" is a plain string because a locale is requested;
                        # be defensive in case Blizzard ever returns a dict here.
                        if isinstance(name, dict):
                            name = name.get("en_US") or next(iter(name.values()), None)
                        if name and slug:
                            seen[slug] = name
                    realms = sorted(
                        ({"name": n, "slug": s} for s, n in seen.items()),
                        key=lambda x: x["name"].lower(),
                    )
    except Exception:
        realms = []
    if realms:
        _REALM_CACHE[key] = (now, realms)
    return realms


async def fetch_realm_names(get_token, region: str) -> list[str]:
    """Realm display names for ``region`` (see :func:`fetch_realm_index`)."""
    return [r["name"] for r in await fetch_realm_index(get_token, region)]


def cached_realm_slug(region: Optional[str], realm: str) -> Optional[str]:
    """Slug of ``realm`` from the cached realm index (no network), or None.

    Matches the display name (case-insensitive) or an already valid slug.
    """
    hit = _REALM_CACHE.get(f"{GAME}:{(region or '').lower()}")
    if not hit or not realm:
        return None
    want = str(realm).strip().casefold()
    for r in hit[1]:
        if r["slug"].casefold() == want or r["name"].casefold() == want:
            return r["slug"]
    return None


async def realm_choices(
    get_token, regions, current: str, *, with_region: bool = False, limit: int = 25
) -> list[app_commands.Choice[str]]:
    """Autocomplete choices: realm name as label, Blizzard realm slug as value.

    With ``with_region`` the value is ``"{slug}:{region}"`` and the label gets the
    region appended (used by commands that take ``Realm:Region`` in one option).
    """
    if isinstance(regions, str):
        regions = [regions]
    cur = (current or "").casefold()
    out: list[app_commands.Choice[str]] = []
    for region in regions:
        region = (region or "eu").lower()
        for r in await fetch_realm_index(get_token, region):
            if cur and cur not in r["name"].casefold() and cur not in r["slug"]:
                continue
            if with_region:
                label = f"{r['name']} ({region.upper()})"
                value = f"{r['slug']}:{region}"
            else:
                label, value = r["name"], r["slug"]
            out.append(app_commands.Choice(name=label[:100], value=value[:100]))
            if len(out) >= limit:
                return out
    return out


_EXPENSIONS = {
    "Classic",
    "The Burning Crusade",
    "Wrath of the Lich King",
    "Cataclysm",
    "Mists of Pandaria",
}

import time

import aiohttp

REALMS: dict[str, list[str]] = {
  "Amnennar": ["EU"],
  "Angerforge": ["US"],
  "Arugal": ["US"],
  "Ashbringer": ["EU"],
  "Ashkandi": ["US"],
  "Atiesh": ["US"],
  "Auberdine": ["EU"],
  "Azuresong": ["US"],
  "Benediction": ["US"],
  "Bloodsail Buccaneers": ["US"],
  "Chromie": ["EU", "RU"],
  "Earthfury": ["US"],
  "Earthshaker": ["EU"],
  "Eranikus": ["US"],
  "Everlook": ["EU"],
  "Faerlina": ["US"],
  "Firemaw": ["EU"],
  "Flamegor": ["EU", "RU"],
  "Gehennas": ["EU"],
  "Giantstalker": ["EU"],
  "Golemagg": ["EU"],
  "Grobbulus": ["US"],
  "Hydraxian Waterlords": ["EU"],
  "Jin'do": ["EU"],
  "Lakeshire": ["EU"],
  "Maladath": ["US"],
  "Mandokir": ["EU", "ES"],
  "Mankrik": ["US"],
  "Mirage Raceway": ["EU"],
  "Mograine": ["EU"],
  "Myzrael": ["US"],
  "Nethergarde Keep": ["EU"],
  "Old Blanchy": ["US"],
  "Pagle": ["US"],
  "Patchwerk": ["EU"],
  "Pyrewood Village": ["EU"],
  "Razorfen": ["EU"],
  "Remulos": ["US"],
  "Skyfury": ["US"],
  "Sulfuras": ["US"],
  "Sulfuron": ["EU"],
  "Thekal": ["EU"],
  "Transcendence": ["EU"],
  "Venoxis": ["EU"],
  "Westfall": ["US"],
  "Whitemane": ["US"],
  "Windseeker": ["US"],
  "Yojamba": ["US"],
  "伊弗斯": ["TW"],
  "古雷曼格": ["TW"],
  "札里克": ["TW"],
  "烏蘇雷": ["TW"],
  "瑪拉頓": ["TW"],
  "逐風者": ["TW"],
  "阿拉希盆地": ["TW"],
  "魚人": ["TW"],
  "라그나로스": ["KR"],
  "로크홀라": ["KR"],
  "서리한": ["KR"],
  "소금 평원": ["KR"],
  "얼음피": ["KR"],
}

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
GAME = "classic"  # wowtools_retail -> "retail" | wowtools_classic -> "classic"

_REALM_CACHE: dict[str, tuple[float, list[str]]] = {}
_REALM_TTL = 21600  # seconds (6h)


async def fetch_realm_names(get_token, region: str) -> list[str]:
    """Live realm names for ``region`` using the namespace of this cog's game.

    ``get_token`` is an async callable ``(region) -> bearer token``. Returns an
    empty list on any error so the autocomplete never breaks.
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
    names: list[str] = []
    try:
        token = await get_token(region)
        url = f"https://{host}/data/wow/realm/index"
        params = {"namespace": namespace, "locale": "en_US"}
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    js = await resp.json()
                    names = sorted(
                        {r.get("name") for r in js.get("realms", []) if r.get("name")}
                    )
    except Exception:
        names = []
    if names:
        _REALM_CACHE[key] = (now, names)
    return names


_EXPENSIONS = {
    "Classic",
    "The Burning Crusade",
    "Wrath of the Lich King",
    "Cataclysm",
    "Mists of Pandaria",
}

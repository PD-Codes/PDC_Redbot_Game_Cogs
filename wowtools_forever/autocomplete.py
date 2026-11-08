# WoW Forever has no realms: characters are addressed by "name-surname" per region
# and guilds by their guild slug. The realm list/autocomplete of the Classic cog was
# therefore removed on purpose.

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

# wowtools_retail -> "retail" | wowtools_classic -> "classic" | wowtools_forever -> "forever"
GAME = "forever"

# Build string used for the versioned static namespace
# ("static-<build>-forever-<region>"). Community tests (Nov 2026) report that the
# plain "static-forever-<region>" namespace answers 403 while the versioned one works.
# NOTE: This value MUST be updated whenever WoW Forever receives a new client patch/build.
FOREVER_STATIC_BUILD = "1.60.1_70399"

# WoW Forever is based on the original game (level cap 60), so the raid statistics
# only contain the base expansion category.
_EXPENSIONS = {
    "Classic",
}

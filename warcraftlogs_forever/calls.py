# Most of the source of this file can be found at:
# https://github.com/Kowlin/GraphQL-WoWLogs/blob/master/wowlogs/calls.py
#
# WoW: Forever variant.
# WoW: Forever has no realms (characters are "Name Surname", unique per region),
# so the character selector of every query is built dynamically from a
# "character reference" (char_ref) dict instead of a fixed
# name/serverSlug/serverRegion triple. Arguments that are not set are OMITTED
# from the query (not passed as explicit null), because the WCL v2 schema
# declares all character() arguments as optional:
#     character(id: Int, name: String, serverSlug: String, serverRegion: String)
# Whether forever.warcraftlogs.com actually resolves characters without a
# serverSlug is NOT documented (see lookup.py).

from typing import Dict, Tuple

# mode -> (GraphQL variable definitions, character() arguments, variable keys)
_CHAR_SELECTORS: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    "id": ("$char_id: Int!", "id: $char_id", ("char_id",)),
    "slug": (
        "$char_name: String!, $char_realm: String!, $char_server: String!",
        "name: $char_name, serverSlug: $char_realm, serverRegion: $char_server",
        ("char_name", "char_realm", "char_server"),
    ),
    "noslug": (
        "$char_name: String!, $char_server: String!",
        "name: $char_name, serverRegion: $char_server",
        ("char_name", "char_server"),
    ),
}


def selector_mode(char_ref: dict) -> str:
    """Pick the character() selector mode for a char_ref dict."""
    if char_ref.get("char_id") is not None:
        return "id"
    if char_ref.get("char_realm"):
        return "slug"
    return "noslug"


def build_query(template: str, char_ref: dict, extra_vardefs: str = "") -> str:
    """Fill the __VARDEFS__ / __CHARARGS__ placeholders of a query template."""
    vardefs, args, _keys = _CHAR_SELECTORS[selector_mode(char_ref)]
    if extra_vardefs:
        vardefs = f"{vardefs}, {extra_vardefs}"
    return template.replace("__VARDEFS__", vardefs).replace("__CHARARGS__", args)


def char_variables(char_ref: dict) -> dict:
    """Return only the GraphQL variables used by the selected selector mode."""
    _vardefs, _args, keys = _CHAR_SELECTORS[selector_mode(char_ref)]
    return {k: char_ref[k] for k in keys}


class Queries:
    # Light query used by the character resolver (lookup.py).
    # ``server`` is read defensively: for Forever it may describe a
    # region-wide placeholder server (unknown, undocumented).
    find_character = """
    query (__VARDEFS__) {
  characterData {
    character(__CHARARGS__) {
      id
      name
      classID
      server {
        name
        slug
        region {
          slug
        }
      }
    }
  }
}
"""

    get_last_encounter = """
    query (__VARDEFS__) {
  rateLimitData {
    limitPerHour
    pointsSpentThisHour
    pointsResetIn
  }
  characterData {
    character(__CHARARGS__) {
      name
      id
      classID
      recentReports(limit: 1) {
        data {
          fights(killType: Kills) {
            encounterID
            name
            endTime
          }
        }
      }
    }
  }
}
"""

    get_overview = """
    query (__VARDEFS__) {
  rateLimitData {
    limitPerHour
    pointsSpentThisHour
    pointsResetIn
  }
  characterData {
    character(__CHARARGS__) {
      name
      id
      zoneRankings(zoneID: $zone_id, difficulty: $difficulty)
      }
    }
  }
"""
    get_overview_vardefs = "$zone_id: Int!, $difficulty: Int!"

    get_gear = """
    query (__VARDEFS__) {
  rateLimitData {
    limitPerHour
    pointsSpentThisHour
    pointsResetIn
  }
  characterData {
    character(__CHARARGS__) {
      name
      id
      classID
      encounterRankings(includeCombatantInfo: true, byBracket: true, encounterID: $encounter)
    }
  }
}
"""
    get_gear_vardefs = "$encounter: Int!"

    check_bearer = """
    query {
  rateLimitData {
    limitPerHour
    pointsSpentThisHour
    pointsResetIn
  }
}
"""

    # Live zone list (raids/dungeons of forever.warcraftlogs.com). Forever zone
    # and encounter IDs are unknown and will grow with new content, so they
    # are never hardcoded.
    get_zones = """
    query {
  worldData {
    zones {
      id
      name
      frozen
      difficulties {
        id
        name
      }
      encounters {
        id
        name
      }
    }
  }
}
"""

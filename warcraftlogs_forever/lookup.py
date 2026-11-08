"""Character lookup for WoW: Forever on forever.warcraftlogs.com.

WoW: Forever has no realms. A character is "Name Surname", unique per region,
and Blizzard's character slug is ``{name}-{surname}`` (lowercase).

How Warcraft Logs identifies Forever characters in
``characterData.character(name:, serverSlug:, serverRegion:)`` is NOT
documented. Everything in ``CharacterResolver`` is therefore an
UNDOCUMENTED, BEST-EFFORT strategy: several argument variants are tried until
one returns a non-null character. The successful variant is remembered per
region (in memory only), so later lookups try it first.

Lookups by WCL character id (number or ``/character/id/<id>`` link) and by a
full WCL character link (``/character/<region>/<server>/<name>``) do not
depend on any of these guesses and are the reliable fallback for users.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import unquote

log = logging.getLogger("red.karlo-cogs.warcraftlogs-forever.lookup")

_LINK_ID_RE = re.compile(r"/character/id/(\d+)", re.IGNORECASE)
_LINK_PATH_RE = re.compile(
    r"/character/([a-z]{2})/([^/?#\s>]+)/([^/?#\s>]+)", re.IGNORECASE
)

# Static server-slug guesses tried when nothing better is known for a region.
# Pure guesses (undocumented); learned slugs from successful lookups are
# tried first.
STATIC_SLUG_GUESSES: Tuple[str, ...] = ("forever",)
# Cap for learned slugs per region (keeps the number of probe queries small).
MAX_KNOWN_SLUGS = 5
# Seconds a name that was cleanly "not found" is remembered, so repeated
# lookups of the same unknown name do not re-run every probe query.
NEGATIVE_CACHE_TTL = 600


def parse_character_input(text: Optional[str]) -> Optional[dict]:
    """Return a char_ref when ``text`` is a WCL character link or a numeric id.

    Returns None when ``text`` should be treated as a plain character name.
    NOTE: links/ids of other WCL sub-sites (classic, www, ...) are accepted as
    well; whether character ids are shared between sub-sites is unknown.
    """
    if not text:
        return None
    t = text.strip().strip("<>").strip()
    if t.isdigit():
        return {"char_id": int(t)}
    lowered = t.lower()
    if "warcraftlogs.com" not in lowered and not lowered.startswith("/character/"):
        return None
    m = _LINK_ID_RE.search(t)
    if m:
        return {"char_id": int(m.group(1))}
    m = _LINK_PATH_RE.search(t)
    if m:
        region, server, name = m.groups()
        return {
            "char_name": unquote(name),
            "char_realm": unquote(server).lower(),
            "char_server": region.upper(),
        }
    return None


def slugify(value: str) -> str:
    """Blizzard-style slug: lowercase, apostrophes removed, spaces to dashes."""
    value = (value or "").strip().lower()
    value = value.replace("'", "").replace("’", "")
    return re.sub(r"\s+", "-", value)


def normalize_name(value: Optional[str]) -> Optional[str]:
    """Capitalize the first letter only (keeps names like "O'neil" intact)."""
    if not value:
        return value
    value = value.strip()
    return value[:1].upper() + value[1:].lower()


def split_name(name: str, surname: Optional[str]) -> Tuple[str, Optional[str]]:
    """Accept "Name Surname" or "name-surname" (Blizzard slug) in one argument."""
    name = (name or "").strip()
    if surname:
        return name, surname.strip()
    if " " in name:
        first, rest = name.split(None, 1)
        return first, rest.strip() or None
    if "-" in name:
        first, rest = name.split("-", 1)
        return first, rest.strip() or None
    return name, None


@dataclass
class LookupResult:
    character: Optional[dict] = None
    error: Optional[str] = None
    auth_error: bool = False
    tried: List[str] = field(default_factory=list)


class CharacterResolver:
    """UNDOCUMENTED, BEST-EFFORT resolver for Forever characters.

    Keeps three in-memory caches (lost on cog reload, by design):
    - ``_variant_by_region``: region -> variant key that worked last time
    - ``_known_slugs``: region -> server slugs seen on resolved characters
    - ``_resolved``: (region, name, surname) -> resolved character dict
    - ``_misses``: (region, name, surname) -> monotonic time of a clean miss
      (negative cache, ``NEGATIVE_CACHE_TTL`` seconds)
    """

    def __init__(self) -> None:
        self._variant_by_region: Dict[str, str] = {}
        self._known_slugs: Dict[str, List[str]] = {}
        self._resolved: Dict[Tuple[str, str, str], dict] = {}
        self._misses: Dict[Tuple[str, str, str], float] = {}

    # ------------------------------------------------------------------ #
    def learn(self, character: Optional[dict], region: Optional[str] = None) -> None:
        """Remember the server slug of a resolved character (read defensively)."""
        if not isinstance(character, dict):
            return
        server = character.get("server") or {}
        slug = server.get("slug") if isinstance(server, dict) else None
        reg = None
        if isinstance(server, dict):
            reg = (server.get("region") or {}).get("slug") if isinstance(server.get("region"), dict) else None
        reg = (reg or region or "").upper()
        if not slug or not reg:
            return
        slugs = self._known_slugs.setdefault(reg, [])
        if slug not in slugs:
            slugs.insert(0, slug)
            del slugs[MAX_KNOWN_SLUGS:]

    def _slug_guesses(self, region: str, name: str, surname: Optional[str]) -> List[str]:
        own: Set[str] = {slugify(surname or "")}
        if surname:
            own.add(f"{slugify(name)}-{slugify(surname)}")
        out: List[str] = []
        for slug in list(self._known_slugs.get(region, [])) + list(STATIC_SLUG_GUESSES):
            if slug and slug not in own and slug not in out:
                out.append(slug)
        return out

    def _candidates(self, name: str, surname: Optional[str], region: str) -> List[Tuple[str, dict]]:
        """Ordered (variant_key, char_ref) list to probe."""
        full = f"{name} {surname}" if surname else name
        cands: List[Tuple[str, dict]] = []
        if surname:
            # (a) "Name Surname" without serverSlug, region only
            cands.append(("full_noslug", {"char_name": full, "char_server": region}))
            # (b) "Name" with serverSlug = surname slug
            cands.append(("name_surnameslug", {
                "char_name": name, "char_realm": slugify(surname), "char_server": region,
            }))
            # (c) "Name" with serverSlug = Blizzard character slug "{name}-{surname}"
            cands.append(("name_charslug", {
                "char_name": name,
                "char_realm": f"{slugify(name)}-{slugify(surname)}",
                "char_server": region,
            }))
        else:
            cands.append(("name_noslug", {"char_name": name, "char_server": region}))
        # (d) region-wide placeholder slugs learned from data (+ static guesses)
        for slug in self._slug_guesses(region, name, surname):
            if surname:
                cands.append((f"full_slug:{slug}", {
                    "char_name": full, "char_realm": slug, "char_server": region,
                }))
            cands.append((f"name_slug:{slug}", {
                "char_name": name, "char_realm": slug, "char_server": region,
            }))

        # Previously successful variant for this region goes first.
        preferred = self._variant_by_region.get(region)
        if preferred:
            cands.sort(key=lambda c: 0 if c[0] == preferred else 1)
        return cands

    # ------------------------------------------------------------------ #
    async def resolve_ref(self, http, char_ref: dict) -> LookupResult:
        """Resolve an explicit reference (id or parsed WCL link). No guessing."""
        res = LookupResult(tried=[f"ref:{sorted(char_ref)}"])
        answer = await http.find_character(char_ref)
        if answer is None:
            res.error, res.auth_error = "Bearer token invalid (non-JSON response)", True
            return res
        if answer.get("error"):
            res.error, res.auth_error = answer["error"], bool(answer.get("auth"))
            return res
        res.character = answer.get("character")
        self.learn(res.character, char_ref.get("char_server"))
        return res

    async def resolve_name(
        self, http, name: str, surname: Optional[str], region: str
    ) -> LookupResult:
        """Probe the name variants (undocumented, best effort)."""
        name = normalize_name(name)
        surname = normalize_name(surname) if surname else None
        region = (region or "").upper()
        key = (region, name.lower(), (surname or "").lower())
        if key in self._resolved:
            return LookupResult(character=self._resolved[key], tried=["cache"])
        now = time.monotonic()
        missed_at = self._misses.get(key)
        if missed_at is not None:
            if now - missed_at < NEGATIVE_CACHE_TTL:
                return LookupResult(tried=["negative-cache"])
            del self._misses[key]

        res = LookupResult()
        clean_miss = False  # at least one variant answered "no such character"
        for variant, ref in self._candidates(name, surname, region):
            res.tried.append(variant)
            answer = await http.find_character(ref)
            if answer is None:
                res.error, res.auth_error = "Bearer token invalid (non-JSON response)", True
                return res
            if answer.get("auth"):
                res.error, res.auth_error = answer.get("error"), True
                return res
            if answer.get("error"):
                # e.g. the API refuses a selector without serverSlug: try the next one.
                log.debug("Forever lookup variant %s failed: %s", variant, answer["error"])
                res.error = answer["error"]
                continue
            character = answer.get("character")
            if character:
                log.info("Forever lookup: %s %s (%s) resolved via variant %s",
                         name, surname or "", region, variant)
                self._variant_by_region[region] = variant
                self._resolved[key] = character
                self.learn(character, region)
                res.character, res.error = character, None
                return res
            clean_miss = True
        if clean_miss:
            # Only report API errors when no variant gave a clean "not found".
            res.error = None
            # Drop expired entries so the negative cache cannot grow unbounded.
            for k in [k for k, t in self._misses.items() if now - t >= NEGATIVE_CACHE_TTL]:
                del self._misses[k]
            self._misses[key] = now
        return res

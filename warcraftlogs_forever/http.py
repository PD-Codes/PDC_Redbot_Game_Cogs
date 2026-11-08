# Most of this code is copied from
# https://github.com/Kowlin/Sentinel/blob/master/githubcards/http.py

# Most of the source of this file can be found at:
# https://github.com/Kowlin/GraphQL-WoWLogs/blob/master/wowlogs/http.py
#
# WoW: Forever variant (forever.warcraftlogs.com).

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, Set, Union

import aiohttp
from redbot.core.bot import Red
from redbot.core.config import Config

from .calls import Queries, build_query, char_variables

log = logging.getLogger("red.karlo-cogs.warcraftlogs-forever.http")

# ASSUMPTION: forever.warcraftlogs.com exposes the same v2 GraphQL API and
# OAuth endpoints as the other WCL sub-sites (classic/fresh/sod/vanilla).
# This is not documented anywhere at the time of writing (Nov 2026).
baseurl = "https://forever.warcraftlogs.com"
graphql_url = baseurl + "/api/v2/client"
oauth_url = baseurl + "/oauth/token"

USER_AGENT = "Red-DiscordBot/WarcraftLogsForeverCog"

# Shown to users when WCL rejects our credentials/bearer.
# NOTE: WCL API clients are created on www.warcraftlogs.com and are believed to
# be valid for every WCL sub-site (the classic and retail cogs already share
# them). If forever.warcraftlogs.com does NOT accept them, every call answers
# 401 and this message is surfaced.
AUTH_ERROR = (
    "401 Unauthorized: forever.warcraftlogs.com rejected the API credentials/bearer. "
    "The shared `warcraftlogs` client_id/client_secret may not be valid for the Forever site."
)

ApiResult = Optional[Union[dict, bool]]
# Async callback of the cog: regenerates the bearer (serialized by the cog's
# token lock), swaps the session and returns the new bearer or None.
Refresher = Callable[[Optional[str]], Awaitable[Optional[str]]]

# Seconds an old session stays open after a bearer swap, so requests that are
# still in flight on it can finish before it gets closed.
SESSION_CLOSE_GRACE = 30
# Total timeout (seconds) for a single HTTP request to Warcraft Logs.
REQUEST_TIMEOUT = 30


def _graphql_errors(json: dict) -> Optional[str]:
    """Extract GraphQL errors (the WCL API returns them as a list under "errors")."""
    errors = json.get("errors", None)
    if not errors:
        return None
    return "; ".join(
        e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errors
    )


async def generate_bearer(bot: Red, config: Config) -> Optional[str]:
    """Generate the Bearer token used in GraphQL queries.

    client_id/client_secret are SHARED with the classic and retail cogs via
    ``get_shared_api_tokens("warcraftlogs")``. The bearer itself is issued by
    forever.warcraftlogs.com and stored ONLY in this cog's own Config
    (``config.bearer`` / ``config.bearer_timestamp``) so it never collides with
    the classic (shared tokens) or retail (own Config) bearer."""
    tokens = await bot.get_shared_api_tokens("warcraftlogs")

    client_id = tokens.get("client_id", "")
    client_secret = tokens.get("client_secret", "")
    if not client_id:
        log.error("Generate bearer: No valid client ID set")
        return None
    if not client_secret:
        log.error("Generate bearer: No valid client secret set")
        return None

    headers = {"User-Agent": USER_AGENT}
    try:
        async with aiohttp.ClientSession(
            headers=headers,
            auth=aiohttp.BasicAuth(login=client_id, password=client_secret),
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as session:
            form = aiohttp.FormData()
            form.add_field("grant_type", "client_credentials")
            async with session.post(oauth_url, data=form) as request:
                if request.status == 401:
                    log.error(
                        "Forever OAuth returned 401: the shared WCL client is not "
                        "accepted by %s (wrong id/secret, or not valid for this sub-site).",
                        baseurl,
                    )
                    return None
                try:
                    json = await request.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    log.error(
                        "Forever OAuth endpoint %s returned a non-JSON response (HTTP %s). "
                        "The endpoint may not exist for this sub-site.",
                        oauth_url,
                        request.status,
                    )
                    return None
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        log.error("Forever OAuth request failed: %r", exc)
        return None

    if not isinstance(json, dict) or json.get("error", ""):
        log.error(
            "There is an error generating the bearer key, "
            "probably a misconfigured client id and secret."
        )
        if isinstance(json, dict):
            log.error(f"{json.get('error')}: {json.get('error_description')}")
        return None

    access_token = json.get("access_token")
    if not access_token:
        log.error("Forever OAuth response did not contain an access_token")
        return None

    timestamp_now = int(datetime.now(timezone.utc).timestamp())
    # Reduce the lifetime by 2 min to be on the safe side of possible errors.
    try:
        expires_in = int(json.get("expires_in", 3600))
    except (TypeError, ValueError):
        expires_in = 3600
    bearer_timestamp = timestamp_now + expires_in - 120
    await config.bearer_timestamp.set(bearer_timestamp)
    # Own Config only -- never the shared "warcraftlogs" tokens.
    await config.bearer.set(access_token)

    log.info("Forever bearer token created")
    return access_token


class WoWLogsClient:
    """This is where the magic happens."""

    def __init__(self, bearer: Optional[str], refresher: Optional[Refresher] = None) -> None:
        self.session: aiohttp.ClientSession
        self._bearer: Optional[str] = None
        self._refresher = refresher
        self._close_tasks: Set[asyncio.Task] = set()
        self._create_session(bearer)

    def _create_session(self, bearer: Optional[str]) -> None:
        headers = {
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
            # Not strictly required to set an user agent, yet still respectful.
            "User-Agent": USER_AGENT,
        }
        self._bearer = bearer
        self.session = aiohttp.ClientSession(
            headers=headers, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        )

    async def recreate_session(self, bearer: Optional[str]) -> None:
        """Swap in a session with a new bearer.

        The old session is closed only AFTER the swap and after a grace period,
        so concurrent requests that still use it are not cut off."""
        old = getattr(self, "session", None)
        self._create_session(bearer)
        if old is not None and not old.closed:
            task = asyncio.create_task(self._close_later(old))
            self._close_tasks.add(task)
            task.add_done_callback(self._close_tasks.discard)

    @staticmethod
    async def _close_later(session: aiohttp.ClientSession) -> None:
        try:
            await asyncio.sleep(SESSION_CLOSE_GRACE)
        finally:
            if not session.closed:
                await session.close()

    async def close(self) -> None:
        """Close the current session (old sessions are closed by their tasks)."""
        for task in list(self._close_tasks):
            task.cancel()
        if not self.session.closed:
            await self.session.close()

    async def _post(
        self, query: str, variables: Optional[dict] = None, *, retry: bool = True
    ) -> Optional[dict]:
        """POST a GraphQL query.

        Returns:
            None  -> response was not JSON (bearer invalidated)
            {"error": str, "auth": True} -> HTTP 401/403 (also after one renewal)
            {"error": str} -> API, GraphQL or network error
            dict  -> the raw JSON response on success

        On 401/403 the bearer is regenerated once via the cog's refresher and the
        request is retried once.
        """
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        session, bearer = self.session, self._bearer
        status = 0
        auth_failed = False
        non_json = False
        json = None
        try:
            async with session.post(graphql_url, json=payload) as call:
                status = call.status
                if status in (401, 403):
                    log.error("GraphQL returned HTTP %s", status)
                    auth_failed = True
                else:
                    try:
                        json = await call.json()
                    except (aiohttp.ContentTypeError, ValueError):
                        log.error("Non-JSON response from the WCL API (HTTP %s)", status)
                        non_json = True
                        # ASSUMPTION: WCL may answer an invalid bearer with a non-JSON
                        # page instead of a 401 (the original code treated any non-JSON
                        # answer as "bearer invalidated"). Rate limits (429) and server
                        # errors (5xx, e.g. Cloudflare pages) are not auth problems.
                        auth_failed = status < 429
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as exc:
            # RuntimeError("Session is closed"): the session was swapped and closed
            # while this request was starting -> retry once with the new session.
            if retry and self.session is not session:
                return await self._post(query, variables, retry=False)
            log.error("GraphQL request failed: %r", exc)
            return {"error": str(exc) or exc.__class__.__name__}

        if auth_failed:
            if retry and self._refresher is not None:
                new_bearer = await self._refresher(bearer)
                if new_bearer:
                    return await self._post(query, variables, retry=False)
            if non_json:
                return None
            return {"error": AUTH_ERROR, "auth": True}
        if non_json:
            return {"error": f"Unexpected non-JSON response from the WCL API (HTTP {status})"}

        if not isinstance(json, dict):
            return {"error": "Unexpected response from the WCL API"}

        error = json.get("error", None)
        if error:
            log.error(f"Error: {error}")
            return {"error": str(error)}

        gql_errors = _graphql_errors(json)
        if gql_errors:
            log.error(f"GraphQL error: {gql_errors}")
            return {"error": gql_errors}

        return json

    async def check_bearer(self) -> bool:
        json = await self._post(Queries.check_bearer)
        if json is None or json.get("auth"):
            log.error("Bearer token has been invalidated")
            return False
        return True

    async def get_zones(self) -> Optional[list]:
        """Fetch the list of WCL zones live from the API (cached by the cog).

        Returns the list under ``data.worldData.zones`` or ``None`` on error."""
        json = await self._post(Queries.get_zones)
        if json is None or json.get("error"):
            return None
        try:
            zones = json["data"]["worldData"]["zones"]
        except (KeyError, TypeError):
            return None
        # Coerce zone/encounter/difficulty ids to int defensively and skip
        # entries without a usable id (the cog sorts and compares them as ints).
        out = []
        for z in zones or []:
            if not isinstance(z, dict):
                continue
            try:
                zone = dict(z, id=int(z["id"]))
            except (KeyError, TypeError, ValueError):
                continue
            for key in ("difficulties", "encounters"):
                items = []
                for entry in z.get(key) or []:
                    try:
                        items.append(dict(entry, id=int(entry["id"])))
                    except (KeyError, TypeError, ValueError):
                        continue
                zone[key] = items
            out.append(zone)
        return out

    async def find_character(self, char_ref: dict) -> Optional[dict]:
        """Single lookup attempt used by the resolver in lookup.py.

        Returns None (non-JSON), an error dict, or ``{"character": obj|None}``."""
        json = await self._post(
            build_query(Queries.find_character, char_ref), char_variables(char_ref)
        )
        if json is None or json.get("error"):
            return json
        try:
            return {"character": json["data"]["characterData"]["character"]}
        except (KeyError, TypeError):
            return {"error": "Unexpected response structure from the WCL API"}

    async def get_overview(self, char_ref: dict, zone_id: int, difficulty: int) -> ApiResult:
        variables = char_variables(char_ref)
        variables.update({"zone_id": zone_id, "difficulty": difficulty})
        return await self._post(
            build_query(Queries.get_overview, char_ref, Queries.get_overview_vardefs),
            variables,
        )

    async def get_last_encounter(self, char_ref: dict) -> ApiResult:
        json = await self._post(
            build_query(Queries.get_last_encounter, char_ref), char_variables(char_ref)
        )
        if json is None or json.get("error"):
            return json

        try:
            character = json["data"]["characterData"]["character"]
            if character is None:
                return False

            data = (character.get("recentReports") or {}).get("data") or []
            unique_encouters = {"ids": [], "latest": 0, "latest_time": 0}
            if not data:
                return unique_encouters
            for fight in data[0].get("fights") or []:
                enc_id = int(fight.get("encounterID") or 0)
                if not enc_id:
                    continue
                if enc_id not in unique_encouters["ids"]:
                    unique_encouters["ids"].append(enc_id)
                end_time = fight.get("endTime") or 0
                if end_time > unique_encouters["latest_time"]:
                    unique_encouters["latest"] = enc_id
                    unique_encouters["latest_time"] = end_time
        except (KeyError, TypeError, IndexError, ValueError):
            log.error("Unexpected response structure from the WCL API")
            return {"error": "Unexpected response structure from the WCL API"}
        return unique_encouters

    async def get_gear(self, char_ref: dict, encounter_id: int) -> ApiResult:
        variables = char_variables(char_ref)
        variables["encounter"] = encounter_id
        json = await self._post(
            build_query(Queries.get_gear, char_ref, Queries.get_gear_vardefs), variables
        )
        if json is None or json.get("error"):
            return json

        try:
            data = json["data"]["characterData"]["character"]
        except (KeyError, TypeError):
            log.error("Unexpected response structure from the WCL API")
            return {"error": "Unexpected response structure from the WCL API"}
        if data is None:
            return False
        return data

# Most of this code is copied from
# https://github.com/Kowlin/Sentinel/blob/master/githubcards/http.py

# Most of the source of this file can be found at:
# https://github.com/Kowlin/GraphQL-WoWLogs/blob/master/wowlogs/http.py

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, Set, Union

import aiohttp
from redbot.core.bot import Red
from redbot.core.config import Config

from .calls import Queries

log = logging.getLogger("red.karlo-cogs.warcraftlogs-retail.http")
baseurl = "https://www.warcraftlogs.com"
graphql_url = baseurl + "/api/v2/client"
oauth_url = baseurl + "/oauth/token"

USER_AGENT = "Red-DiscordBot/WarcraftLogsCog"

# Seconds an old session stays open after a bearer swap, so requests that are
# still in flight on it can finish before it gets closed.
SESSION_CLOSE_GRACE = 30
# Total timeout (seconds) for a single HTTP request to Warcraft Logs.
REQUEST_TIMEOUT = 30

# Shown to users when WCL rejects our credentials/bearer.
AUTH_ERROR = "401 Unauthorized: www.warcraftlogs.com rejected the API credentials/bearer."

ApiResult = Optional[Union[dict, bool]]
# Async callback of the cog: regenerates the bearer (serialized by the cog's
# token lock), swaps the session and returns the new bearer or None.
Refresher = Callable[[Optional[str]], Awaitable[Optional[str]]]


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

    client_id/client_secret are SHARED with the other Warcraft Logs cogs via
    ``get_shared_api_tokens("warcraftlogs")``. The bearer itself is issued by
    www.warcraftlogs.com and stored ONLY in this cog's own Config
    (``config.bearer`` / ``config.bearer_timestamp``); it is never written to the
    shared tokens, so it cannot clobber the bearers of the other cogs or trigger
    their token-update listeners.

    Never raises: every failure is logged and returns None."""
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
                        "OAuth returned 401: the WCL client id/secret was rejected by %s.",
                        baseurl,
                    )
                    return None
                try:
                    json = await request.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    # e.g. a Cloudflare HTML page instead of JSON.
                    log.error(
                        "OAuth endpoint %s returned a non-JSON response (HTTP %s).",
                        oauth_url,
                        request.status,
                    )
                    return None
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        log.error("OAuth request failed: %r", exc)
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
        log.error("OAuth response did not contain an access_token")
        return None

    timestamp_now = int(datetime.now(timezone.utc).timestamp())
    try:
        expires_in = int(json.get("expires_in", 3600))
    except (TypeError, ValueError):
        expires_in = 3600
    # Reduce the lifetime by 2 min to be on the safe side of possible errors.
    await config.bearer_timestamp.set(timestamp_now + expires_in - 120)
    # Own Config only -- never the shared "warcraftlogs" tokens.
    await config.bearer.set(access_token)

    log.info("Bearer token created")
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

        Zone IDs are WCL-internal (not Blizzard instance ids) and change with
        new content, so they must not be hardcoded. Returns the list under
        ``data.worldData.zones`` or ``None`` on error."""
        json = await self._post(Queries.get_zones)
        if json is None or json.get("error"):
            return None
        try:
            zones = json["data"]["worldData"]["zones"]
        except (KeyError, TypeError):
            return None
        out = []
        for z in zones or []:
            try:
                if isinstance(z, dict) and z.get("id") is not None:
                    int(z["id"])
                    out.append(z)
            except (TypeError, ValueError):
                continue
        return out

    async def get_overview(
        self,
        char_name: str,
        char_realm: str,
        char_server: str,
        zone_id: int,
        difficulty: int,
    ) -> Optional[dict]:
        return await self._post(
            Queries.get_overview,
            {
                "char_name": char_name,
                "char_realm": char_realm,
                "char_server": char_server,
                "zone_id": zone_id,
                "difficulty": difficulty,
            },
        )

    async def get_last_encounter(self, char_name: str, char_realm: str, char_server: str) -> ApiResult:
        """Boss kills of the character's most recent report.

        Returns False if the character does not exist, otherwise
        ``{"ids": [...], "latest": id, "latest_time": ms}`` (``ids`` is empty
        when the last report has no boss kill) or an error dict / None."""
        json = await self._post(
            Queries.get_last_encounter,
            {"char_name": char_name, "char_realm": char_realm, "char_server": char_server},
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
        except (KeyError, TypeError, IndexError, ValueError, AttributeError):
            log.error("Unexpected response structure from the WCL API")
            return {"error": "Unexpected response structure from the WCL API"}
        return unique_encouters

    async def get_gear(
        self, char_name: str, char_realm: str, char_server: str, encounter_id: int
    ) -> ApiResult:
        json = await self._post(
            Queries.get_gear,
            {
                "char_name": char_name,
                "char_realm": char_realm,
                "char_server": char_server,
                "encounter": encounter_id,
            },
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

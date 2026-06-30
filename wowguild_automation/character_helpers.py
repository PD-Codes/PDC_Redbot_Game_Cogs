"""Linked WoW characters per member: Retail + MoP Classic, guild roster validation, uniqueness."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

import discord
from redbot.core import Config

GAME_RETAIL = "retail"
GAME_MOP = "mop_classic"
SUPPORTED_GAMES = (GAME_RETAIL, GAME_MOP)
GAME_LABELS = {GAME_RETAIL: "Retail", GAME_MOP: "MoP Classic"}


def game_label(game_type: str) -> str:
    return GAME_LABELS.get(game_type, game_type)


def char_tuple_key(name: str, game_type: str) -> Tuple[str, str]:
    return (name.strip().lower(), (game_type or GAME_RETAIL).lower())


def normalize_stored_main_characters(raw: Any) -> Dict[str, Dict[str, str]]:
    """
    Liefert main_characters mit kanonischen Keys (retail / mop_classic).
    Ignoriert unbekannte Keys; akzeptiert String-Werte als Name.
    """
    out: Dict[str, Dict[str, str]] = {}
    if not isinstance(raw, dict):
        return out
    for key, v in raw.items():
        ks = str(key).strip().lower().replace(" ", "_").replace("-", "_")
        g: Optional[str] = None
        if ks == GAME_RETAIL or ks in ("r", "wow_retail"):
            g = GAME_RETAIL
        elif ks in (GAME_MOP, "mop", "mopclassic", "classic_mop", "wow_mop"):
            g = GAME_MOP
        elif ks in SUPPORTED_GAMES:
            g = ks
        if g is None:
            continue
        if isinstance(v, str):
            name = v.strip()
            if name:
                out[g] = {"name": name, "game_type": g}
            continue
        if isinstance(v, dict):
            name = str(v.get("name", "")).strip()
            if not name:
                continue
            gt = str(v.get("game_type", g)).lower()
            if gt not in SUPPORTED_GAMES:
                gt = g
            out[g] = {"name": name, "game_type": gt}
    return out


def normalize_linked_characters(raw: Any) -> List[Dict[str, str]]:
    """Migrate legacy list[str] to [{name, game_type}]."""
    if not raw:
        return []
    out: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                n = item.strip()
                if not n:
                    continue
                key = char_tuple_key(n, GAME_RETAIL)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"name": n, "game_type": GAME_RETAIL})
            elif isinstance(item, dict):
                n = str(item.get("name", "")).strip()
                g = str(item.get("game_type", GAME_RETAIL)).lower()
                if g not in SUPPORTED_GAMES:
                    g = GAME_RETAIL
                if not n:
                    continue
                key = char_tuple_key(n, g)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"name": n, "game_type": g})
    return out


async def get_linked_list(member_group: Config) -> List[Dict[str, str]]:
    linked = await member_group.linked_characters()
    if linked:
        return normalize_linked_characters(linked)
    legacy = await member_group.chars()
    return normalize_linked_characters(legacy)


def mains_from_member_data(payload: Dict[str, Any]) -> Dict[str, Optional[Dict[str, str]]]:
    """Build per-game main map from stored member payload (supports legacy main_character)."""
    out: Dict[str, Optional[Dict[str, str]]] = {g: None for g in SUPPORTED_GAMES}
    raw_m = payload.get("main_characters")
    norm_m = normalize_stored_main_characters(raw_m)
    if norm_m:
        for g in SUPPORTED_GAMES:
            v = norm_m.get(g)
            if isinstance(v, dict) and str(v.get("name", "")).strip():
                out[g] = {"name": str(v["name"]).strip(), "game_type": g}
        if any(v is not None for v in out.values()):
            return out
    leg = payload.get("main_character")
    if isinstance(leg, dict) and str(leg.get("name", "")).strip():
        gt = str(leg.get("game_type", GAME_RETAIL)).lower()
        if gt not in SUPPORTED_GAMES:
            gt = GAME_RETAIL
        out[gt] = {"name": str(leg["name"]).strip(), "game_type": gt}
    return out


async def _load_main_characters_raw(member_group: Config) -> Dict[str, Any]:
    """Reads main_characters — without deleting data (no automatic clear on type errors)."""
    try:
        raw = await member_group.main_characters()
    except TypeError:
        return {}
    if raw is None or not isinstance(raw, dict):
        return {}
    return normalize_stored_main_characters(raw)


def _pack_mains_for_storage(mains: Dict[str, Optional[Dict[str, str]]]) -> Dict[str, Dict[str, str]]:
    """Only store set mains — no None entries (avoids Red config errors)."""
    out: Dict[str, Dict[str, str]] = {}
    for g in SUPPORTED_GAMES:
        v = mains.get(g)
        if isinstance(v, dict) and str(v.get("name", "")).strip():
            out[g] = {"name": str(v["name"]).strip(), "game_type": g}
    return out


async def get_main_characters(member_group: Config) -> Dict[str, Optional[Dict[str, str]]]:
    raw = await _load_main_characters_raw(member_group)
    payload = {"main_characters": raw, "main_character": await member_group.main_character()}
    out = mains_from_member_data(payload)
    reg = await member_group.registration()
    if isinstance(reg, dict) and reg.get("type") == "member":
        cn = str(reg.get("char_name") or "").strip()
        if cn:
            sg = str(await member_group.selected_game() or GAME_RETAIL)
            lg = profile_key_to_link_game(sg)
            if out.get(lg) is None:
                out[lg] = {"name": cn, "game_type": lg}
    return out


async def set_main_for_game(member_group: Config, game_type: str, name: str) -> None:
    if game_type not in SUPPORTED_GAMES:
        return
    merged: Dict[str, Optional[Dict[str, str]]] = {g: None for g in SUPPORTED_GAMES}
    raw = await _load_main_characters_raw(member_group)
    for g in SUPPORTED_GAMES:
        v = raw.get(g)
        if isinstance(v, dict) and str(v.get("name", "")).strip():
            merged[g] = {"name": str(v["name"]).strip(), "game_type": g}
    leg = await member_group.main_character()
    if isinstance(leg, dict) and str(leg.get("name", "")).strip():
        lg = str(leg.get("game_type", GAME_RETAIL)).lower()
        if lg not in SUPPORTED_GAMES:
            lg = GAME_RETAIL
        if merged.get(lg) is None:
            merged[lg] = {"name": str(leg["name"]).strip(), "game_type": lg}
    merged[game_type] = {"name": name.strip(), "game_type": game_type}
    await member_group.main_characters.set(_pack_mains_for_storage(merged))
    await member_group.main_character.clear()


async def clear_main_for_game(member_group: Config, game_type: str) -> None:
    raw = await _load_main_characters_raw(member_group)
    new_store: Dict[str, Dict[str, str]] = {}
    for g, v in raw.items():
        if g == game_type:
            continue
        if g in SUPPORTED_GAMES and isinstance(v, dict) and str(v.get("name", "")).strip():
            new_store[g] = {"name": str(v["name"]).strip(), "game_type": g}
    await member_group.main_characters.set(new_store)
    leg = await member_group.main_character()
    if isinstance(leg, dict) and str(leg.get("game_type", "")).lower() == game_type:
        await member_group.main_character.clear()


async def clear_all_mains(member_group: Config) -> None:
    await member_group.main_characters.clear()
    await member_group.main_character.clear()


def profile_key_to_link_game(profile_key: str) -> str:
    """Map wow_profiles key from onboarding to linked_characters game_type (retail / mop_classic)."""
    pk = (profile_key or "").strip().lower()
    if pk == GAME_MOP:
        return GAME_MOP
    return GAME_RETAIL


async def merge_onboarding_character_into_linked(
    config: Config,
    guild: discord.Guild,
    member: discord.Member,
    char_name: str,
    wow_profile_key: str,
) -> bool:
    """
    Persist onboarding main character into linked_characters without a roster API check
    (needed for manual verification and parity with /wow-chars-panel).

    Returns True if the member has this name+game linked after the call (added, or already present).
    Returns False if another member already owns this char+game on the server.
    """
    name = (char_name or "").strip()
    if not name:
        return False
    game_type = profile_key_to_link_game(wow_profile_key)
    member_group = config.member(member)
    linked = await get_linked_list(member_group)
    key = char_tuple_key(name, game_type)
    if any(char_tuple_key(e["name"], e["game_type"]) == key for e in linked):
        return True
    owner = await find_char_owner_guild_wide(config, guild, name, game_type, exclude_user_id=member.id)
    if owner is not None:
        return False
    await set_linked_list(member_group, linked + [{"name": name, "game_type": game_type}])
    return True


async def ensure_main_for_game_if_empty(member_group: Config, game_type: str, char_name: str) -> None:
    """Set main for this game only when none is stored yet."""
    mains = await get_main_characters(member_group)
    if mains.get(game_type) is not None:
        return
    await set_main_for_game(member_group, game_type, (char_name or "").strip())


async def set_linked_list(member_group: Config, items: List[Dict[str, str]]) -> None:
    clean: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for it in items:
        n = str(it.get("name", "")).strip()
        g = str(it.get("game_type", GAME_RETAIL)).lower()
        if g not in SUPPORTED_GAMES or not n:
            continue
        key = char_tuple_key(n, g)
        if key in seen:
            continue
        seen.add(key)
        clean.append({"name": n, "game_type": g})
    await member_group.linked_characters.set(clean)
    await member_group.chars.set([f"{x['name']} ({x['game_type']})" for x in clean])


def format_char_line(
    entry: Dict[str, str],
    mains: Optional[Dict[str, Optional[Dict[str, str]]]] = None,
) -> str:
    tag = f"{entry['name']} ({game_label(entry['game_type'])})"
    if mains:
        m = mains.get(entry["game_type"])
        if m and m.get("name") and char_tuple_key(m["name"], m["game_type"]) == char_tuple_key(
            entry["name"], entry["game_type"]
        ):
            return f"**{tag}** ★ Main-Char"
    return tag


def format_mains_summary(mains: Dict[str, Optional[Dict[str, str]]]) -> str:
    """Eine Zeile: welcher Main pro Spiel gesetzt ist."""
    parts: List[str] = []
    for g in SUPPORTED_GAMES:
        m = mains.get(g)
        if m and str(m.get("name", "")).strip():
            parts.append(f"{game_label(g)}: **{m['name']}**")
    if not parts:
        return "**Main:** — (noch keiner gesetzt)"
    return "**Main:** " + " · ".join(parts)


def format_rank_sync_summary(guild: Optional[discord.Guild], rank_sync_by_game: Any) -> str:
    """Short info from the stored rank-sync state (for lists)."""
    if not isinstance(rank_sync_by_game, dict):
        return ""
    parts: List[str] = []
    for g in SUPPORTED_GAMES:
        st = rank_sync_by_game.get(g)
        if not isinstance(st, dict):
            continue
        title = str(st.get("last_title") or "").strip()
        rid = int(st.get("last_role_id") or 0)
        if not title and not rid:
            continue
        gl = game_label(g)
        role_hint = ""
        if guild is not None and rid:
            role = guild.get_role(rid)
            if role:
                role_hint = f" → {role.name}"
        if title:
            parts.append(f"{gl}: {title}{role_hint}")
        elif role_hint:
            parts.append(f"{gl}:{role_hint}")
    return " · ".join(parts)


async def merge_rank_sync_game_state(
    member_group: Config,
    game: str,
    *,
    last_title: Optional[str] = None,
    last_role_id: Optional[int] = None,
    rank_lock_notice_ts: Optional[float] = None,
) -> None:
    """Aktualisiert rank_sync_by_game[game] ohne None-Blätter zu zerstören."""
    if game not in SUPPORTED_GAMES:
        return
    try:
        raw = await member_group.rank_sync_by_game()
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    cur: Dict[str, Any] = dict(raw.get(game) or {})
    if last_title is not None:
        cur["last_title"] = str(last_title)
    if last_role_id is not None:
        cur["last_role_id"] = int(last_role_id)
    if rank_lock_notice_ts is not None:
        cur["rank_lock_notice_ts"] = float(rank_lock_notice_ts)
    raw[game] = cur
    await member_group.rank_sync_by_game.set(raw)


async def find_char_owner_guild_wide(
    config: Config,
    guild: discord.Guild,
    name: str,
    game_type: str,
    exclude_user_id: Optional[int] = None,
) -> Optional[int]:
    key = char_tuple_key(name, game_type)
    data = await config.all_members(guild)
    for uid_str, payload in data.items():
        try:
            uid = int(uid_str)
        except (TypeError, ValueError):
            continue
        if exclude_user_id is not None and uid == exclude_user_id:
            continue
        linked = normalize_linked_characters(payload.get("linked_characters") or payload.get("chars"))
        for e in linked:
            if char_tuple_key(e["name"], e["game_type"]) == key:
                return uid
    return None


async def wow_profile_for_game(guild_config: Dict[str, Any], game_type: str) -> Optional[Dict[str, Any]]:
    profiles = guild_config.get("wow_profiles") or {}
    if game_type in profiles:
        return profiles[game_type]
    single = guild_config.get("wow") or {}
    if single.get("version") == game_type:
        return single
    return None

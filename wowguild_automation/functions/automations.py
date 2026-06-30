from __future__ import annotations

from dataclasses import dataclass
from typing import Any, FrozenSet, List, Optional, Set, Tuple

import discord

from .blizzard import BlizzardService


def _normalize_rank_entry_list(entries_raw: Any) -> Set[str]:
    if not entries_raw:
        return set()
    if isinstance(entries_raw, str):
        entries_raw = [entries_raw]
    if not isinstance(entries_raw, (list, tuple, set)):
        return set()
    return {str(e).strip().lower() for e in entries_raw if str(e).strip()}


def _rank_matches_normalized_entries(
    normalized: Set[str],
    rank_title: Optional[str],
    rank_index: int,
    api_rank_name: str,
) -> bool:
    if not normalized:
        return False
    candidates: List[str] = []
    if rank_title:
        rt = str(rank_title).strip().lower()
        candidates.append(rt)
        if rt.startswith("rank "):
            candidates.append(rt.replace("rank ", "", 1).strip())
    if api_rank_name:
        an = str(api_rank_name).strip().lower()
        candidates.append(an)
        if an.startswith("rank "):
            candidates.append(an.replace("rank ", "", 1).strip())
    candidates.append(str(int(rank_index)))
    candidates.append(f"rank {int(rank_index)}")
    return any(c and c in normalized for c in candidates)


@dataclass(frozen=True)
class RankSyncPlan:
    """Result of the API/mapping evaluation (without role change)."""

    rank_title: Optional[str]
    target_role_id: int
    mapped_role_ids: FrozenSet[int]
    # True: no Discord role (rank-lock list, protected list, or no mapping).
    protected_skip: bool = False
    # "rank_locked" | "protected" when protected_skip and deliberately skipped.
    skip_reason: Optional[str] = None


class RankSyncService:
    def __init__(self, blizzard: BlizzardService) -> None:
        self.blizzard = blizzard

    def mapped_role_ids_for_profile(self, guild_config: dict, profile_key: str) -> Set[int]:
        rank_mapping_by_profile = guild_config.get("rank_mapping_by_profile", {})
        m = rank_mapping_by_profile.get(profile_key) or guild_config.get("rank_mapping", {})
        out: Set[int] = set()
        for v in m.values():
            try:
                out.add(int(v))
            except (TypeError, ValueError):
                continue
        return out

    def is_rank_rank_locked(
        self,
        guild_config: dict,
        profile_key: str,
        rank_title: Optional[str],
        rank_index: int,
        api_rank_name: str,
    ) -> bool:
        """Guild "rank-lock" list: bot assigns no Discord role for these in-game ranks."""
        raw = guild_config.get("locked_rank_titles_by_profile") or {}
        norm = _normalize_rank_entry_list(raw.get(profile_key))
        return _rank_matches_normalized_entries(norm, rank_title, rank_index, api_rank_name)

    def is_rank_protected(
        self,
        guild_config: dict,
        profile_key: str,
        rank_title: Optional[str],
        rank_index: int,
        api_rank_name: str,
    ) -> bool:
        raw = guild_config.get("protected_rank_titles_by_profile") or {}
        norm = _normalize_rank_entry_list(raw.get(profile_key))
        return _rank_matches_normalized_entries(norm, rank_title, rank_index, api_rank_name)

    async def plan_sync(
        self,
        guild_config: dict,
        main_char: str,
        profile_key: str,
    ) -> RankSyncPlan:
        wow_profiles = guild_config.get("wow_profiles") or {}
        wow = wow_profiles.get(profile_key) or guild_config.get("wow", {})
        if not wow:
            return RankSyncPlan(None, 0, frozenset(), False)

        region = wow.get("region", "")
        version = wow.get("version", profile_key)
        realm = wow.get("realm", "")
        guild_name = wow.get("guild_name", "")
        result = await self.blizzard.search_member(region, version, realm, guild_name, main_char)
        mapped = self.mapped_role_ids_for_profile(guild_config, profile_key)

        if not result:
            return RankSyncPlan(None, 0, frozenset(mapped), False)

        rank_titles_by_profile = guild_config.get("rank_titles_by_profile", {})
        rank_titles = rank_titles_by_profile.get(profile_key) or guild_config.get("rank_titles", {})
        rank_title = rank_titles.get(str(result.rank_index), result.rank_name)
        ri = int(result.rank_index)
        api_rn = str(result.rank_name or "")

        if self.is_rank_rank_locked(
            guild_config,
            profile_key,
            rank_title,
            ri,
            api_rn,
        ):
            return RankSyncPlan(
                rank_title=rank_title,
                target_role_id=0,
                mapped_role_ids=frozenset(mapped),
                protected_skip=True,
                skip_reason="rank_locked",
            )

        if self.is_rank_protected(
            guild_config,
            profile_key,
            rank_title,
            ri,
            api_rn,
        ):
            return RankSyncPlan(
                rank_title=rank_title,
                target_role_id=0,
                mapped_role_ids=frozenset(mapped),
                protected_skip=True,
                skip_reason="protected",
            )

        rank_mapping_by_profile = guild_config.get("rank_mapping_by_profile", {})
        rank_mapping = rank_mapping_by_profile.get(profile_key) or guild_config.get("rank_mapping", {})
        mapped_role_id = rank_mapping.get(rank_title) or rank_mapping.get(result.rank_name)
        member_role_id = guild_config.get("roles", {}).get("member_role_id", 0)
        target_role_id = int(mapped_role_id or member_role_id or 0)

        return RankSyncPlan(
            rank_title=rank_title,
            target_role_id=target_role_id,
            mapped_role_ids=frozenset(mapped),
            protected_skip=False,
        )

    async def apply_plan(
        self,
        member: discord.Member,
        plan: RankSyncPlan,
        *,
        previous_bot_role_id: int = 0,
    ) -> Tuple[bool, str]:
        """
        Applies the plan. Only the rank role last set by the bot is removed
        (previous_bot_role_id), not other mapped roles — manual assignments remain.
        """
        if not plan.rank_title:
            return False, "not_found"
        if not plan.target_role_id:
            return False, "no_role"

        target = member.guild.get_role(int(plan.target_role_id))
        if not target:
            return False, "no_role"

        to_remove: List[discord.Role] = []
        prev = int(previous_bot_role_id or 0)
        if prev and prev != int(plan.target_role_id):
            r_prev = member.guild.get_role(prev)
            if r_prev is not None and r_prev in member.roles:
                to_remove.append(r_prev)

        try:
            if to_remove:
                await member.remove_roles(
                    *to_remove,
                    reason="WoW-Rang-Sync: vorherige Bot-Rang-Rolle ersetzen",
                )
            if target not in member.roles:
                await member.add_roles(
                    target,
                    reason=f"WoW-Rang-Sync: {plan.rank_title}",
                )
        except discord.Forbidden:
            return False, "no_perms"
        except discord.HTTPException:
            return False, "http"

        return True, "ok"

    async def sync_member_rank(
        self,
        member: discord.Member,
        guild_config: dict,
        main_char: str,
        *,
        profile_key: str,
        previous_bot_role_id: int = 0,
    ) -> Tuple[Optional[str], str, int]:
        """
        Plan + apply in one step.
        Returns (rank_title_or_none, reason, applied_role_id on success else 0).
        """
        plan = await self.plan_sync(guild_config, main_char, profile_key)
        if plan.protected_skip:
            return plan.rank_title, (plan.skip_reason or "protected"), 0
        ok, reason = await self.apply_plan(
            member,
            plan,
            previous_bot_role_id=previous_bot_role_id,
        )
        if not ok:
            return plan.rank_title, reason, 0
        return plan.rank_title, "ok", int(plan.target_role_id)

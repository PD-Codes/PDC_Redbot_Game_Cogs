import json
from pathlib import Path
from typing import Any, Dict


class ConfigStore:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path
        self.data_path = self.base_path / "data"
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.config_file = self.data_path / "config.json"
        if not self.config_file.exists():
            self._write(
                {
                    "bot_setup": {
                        "client_id": "",
                        "client_secret": "",
                        "owner_ids": [],
                    },
                    "guilds": {},
                }
            )

    def _read(self) -> Dict[str, Any]:
        with self.config_file.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, payload: Dict[str, Any]) -> None:
        with self.config_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def get_all(self) -> Dict[str, Any]:
        return self._read()

    def update_all(self, payload: Dict[str, Any]) -> None:
        self._write(payload)

    def get_guild(self, guild_id: int) -> Dict[str, Any]:
        data = self._read()
        key = str(guild_id)
        if key not in data["guilds"]:
            data["guilds"][key] = {
                "language": "de-DE",
                "features": {
                    "onboarding": True,
                    "auto_verify": True,
                    "ready_times": True,
                    "sync_rank": True,
                },
                "wow": {
                    "region": "eu",
                    "version": "retail",
                    "realm": "",
                    "guild_name": "",
                },
                "roles": {"guest_role_id": 0, "member_role_id": 0},
                "channels": {
                    "onboarding_channel_id": 0,
                    "manual_review_channel_id": 0,
                    "raid_guest_channel_id": 0,
                },
                "rules": {
                    "rule_channel_id": 0,
                    "rule_emoji": "✅",
                },
                "templates": {
                    "manual_verification": "Manuelle Verifizierung nötig! User {username} hat sich gemeldet als Char {charname} und möchte Gildenrechte erhalten. Bitte bestätigen sie dies manuell."
                },
                "users": {},
            }
            self._write(data)
        return data["guilds"][key]

    def set_guild(self, guild_id: int, guild_payload: Dict[str, Any]) -> None:
        data = self._read()
        data["guilds"][str(guild_id)] = guild_payload
        self._write(data)


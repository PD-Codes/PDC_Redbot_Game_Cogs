from dataclasses import dataclass


@dataclass
class GuildSettingsInput:
    region: str
    version: str
    realm: str
    guild_name: str
    language: str = "en-US"


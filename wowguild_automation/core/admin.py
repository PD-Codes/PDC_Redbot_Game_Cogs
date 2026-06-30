from dataclasses import dataclass


@dataclass
class BotSetupInput:
    client_id: str
    client_secret: str


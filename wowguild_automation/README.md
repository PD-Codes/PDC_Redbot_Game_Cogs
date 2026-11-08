# WoW Guild Automation (Red Cog)

This cog provides WoW guild onboarding, verification, and rank synchronization for Red-DiscordBot.

## Install in Red

Example with a local/path repo:

1. Add repo
   - `[p]repo add pdc-game-cogs https://github.com/PD-Codes/PDC_Redbot_Game_Cogs`
2. Install cog
   - `[p]cog install pdc-game-cogs wowguild-automation`
3. Load cog
   - `[p]load wowguild-automation`

## Setup

- Bot owner setup (global):
  - `/wow-botsetup <client_id> <client_secret>`
  - or `[p]wow-botsetup <client_id> <client_secret>`
- Guild setup (per server):
  - `/wow-guildsettings <region> <version> <realm> <guildname> [language]`
  - or `[p]wow-guildsettings ...`
- Onboarding channel/role wizard (per server):
  - `/wow-onboarding-setup`
  - or `[p]wow onboarding-setup`

## Commands

Two styles are available:

- Grouped:
  - `/wow guildsettings`
  - `/wow readytimes-manage`
  - `/wow chars`
  - `/wow syncrank`
  - `/wow botsetup`
  - `/wow onboarding-setup`
- Slash-style aliases:
  - `/wow-guildsettings`
  - `/wow-readytimes-manage`
  - `/wow-chars` mit Unterbefehlen `list`, `add`, `remove` bzw. Prefix `wow chars …`
  - `/wow-chars-panel` — interaktives Menü (Buttons, ephemeral)
  - `/wow-syncrank`
  - `/wow-botsetup`
  - `/wow-onboarding-setup`

## Onboarding language selection

When a new user joins, onboarding starts in DM and asks for language first:

- `de` -> German (`de-DE`)
- `en` -> US English (`en-US`)

The selected language is stored per member for later use.

## Private onboarding channel flow

`wow-onboarding-setup` supports two modes:

- `create`: bot creates:
  - role `onboarding-new`
  - role `onboarding-complete`
  - channel `onboarding-private`
- `existing`: you provide existing role/channel IDs

Permission model:

- `@everyone`: no access
- `onboarding-new`: can see/write
- `onboarding-complete`: no access

On member join:

- user gets `onboarding-new`
- after onboarding completes, user gets `onboarding-complete`
- `onboarding-new` is removed

## WoW Forever

Supported as version/game key `forever` (label "WoW Forever"), next to `retail` and `mop_classic`.

- Guild setup: pick version `forever`; use `forever` as the profile key too (character linking and rank sync look up the profile by game key). Realm stays empty – Forever has no realms.
- Roster: realm-less `/data/wow/guild/{guild-slug}/roster` with namespace `profile-forever-{region}` (unofficial, community-tested API).
- Characters have first name + surname; only the pair is unique (slug `name-surname`). Linked characters for Forever store an extra `surname` field and are shown as "Name Surname (WoW Forever)". Onboarding asks for first name and surname.
- Characters only appear in the API from level 10 and after logging out once.

## Notes

- Blizzard API integration is currently a functional stub and can be replaced with real OAuth/API calls.
- Manual verification template is configurable in guild config.

## Dashboard integration (AAA3A Dashboard)

This cog now exposes a Dashboard third-party page:

- page name: `wowguild-automation`
- methods: `GET`, `POST`
- context: guild + user (admin/manage_guild or bot owner)

It supports:

- reading current guild config (GET)
- updating guild config fields (POST)
- re-applying onboarding channel permissions after save

Reference:
- [AAA3A Dashboard repository](https://github.com/AAA3A-AAA3A/AAA3A-cogs/tree/main/dashboard)


## PDC_Redbot_Game_Cogs

Game-specific Cogs for Redbot (World of Warcraft, Trivia, …). Split out from the general
[PDC_Redbot_Cogs](https://github.com/PD-Codes/PDC_Redbot_Cogs) repo. Like the rest, these
Cogs are used on my private Discord and are mostly in German :D

> 📖 **Full documentation:** [PDC_Redbot_Cogs Wiki](https://github.com/PD-Codes/PDC_Redbot_Cogs/wiki) (English & Deutsch)
>
> 🧩 **General (non-game) Cogs:** [PDC_Redbot_Cogs](https://github.com/PD-Codes/PDC_Redbot_Cogs)
> — 🌐 **Web dashboard:** [PDC_Redbot_Webapp](https://github.com/PD-Codes/PDC_Redbot_Webapp)

## Install in Red

```
[p]repo add pdc-game-cogs https://github.com/PD-Codes/PDC_Redbot_Game_Cogs
[p]cog install pdc-game-cogs <name>
[p]load <name>
```

## Status Information

| Status | Description |
|---|---|
| Alpha | Alpha Release. Most Commands cannot work |
| Beta | Beta Release. Most Commands should work |
| Info | Not for Production! |
| Release | All Commands should work |
| Stopped | Stopped work on it for different reasons |
| … / On Work | Currently working on it. |

## About Cogs

| Cog | Status / Version | Description | Commands | Author |
|---|---|---|---|---|
| triviagame | Alpha 0.1.0 | Quiz game (`quiz`) with its own question DB (dashboard table) + leaderboard. DE/EN. | `quiz start/stop/leaderboard`, `quizset enable/add/remove/list/language` | pd-codes |
| warcraftlogs_classic | Beta 0.2.2 | Information from Warcraftlogs Classic (commands suffixed `-classic`). Shares the global **Warcraft Logs API** key panel with the retail cog. | `warcraftlogs-classic` (alias `wcl-classic`: `gear`/`rank`), `wclset-classic` | aikaterna (Original) / pd-codes |
| warcraftlogs_retail | Beta 0.1.0 | Information from Warcraftlogs Retail (current retail raid zones fetched dynamically; commands suffixed `-retail`). Shares the global **Warcraft Logs API** key panel with the classic cog. | `warcraftlogs-retail` (alias `wcl-retail`: `gear`/`rank`), `wclset-retail` | pd-codes |
| WoWTools | Beta 0.1.2 | WoW **Retail** tools: ingame stats, information, etc. from WoW characters. Slash commands prefixed `wowt-`; `region` is a dropdown (eu/us/kr/tw). | `wowt-charinfo`, `wowt-charstats`, `wowt-comparechars`, `wowt-cvar`, `wowt-gearcheck`, `wowt-gmanage`, `wowt-gmset`, `wowt-raiderio`, `wowt-raidinfo`, `wowt-rating`, `wowt-sbset`, `wowt-serverset`, `wowt-talentcheck`, `wowt-wowscoreboard`, `wowt-wowtoken` | Karlo (Original) / pd-codes |
| wowtools_classic | Beta 0.1.0 | WoW **Classic** tools: same commands as WoWTools, slash prefixed `wowtc-`. Shares WoWTools' settings (region/realm/API). | `wowtc-charinfo`, `wowtc-charstats`, `wowtc-comparechars`, `wowtc-cvar`, `wowtc-gearcheck`, `wowtc-gmanage`, `wowtc-gmset`, `wowtc-raiderio`, `wowtc-raidinfo`, `wowtc-rating`, `wowtc-sbset`, `wowtc-serverset`, `wowtc-talentcheck`, `wowtc-wowscoreboard`, `wowtc-wowtoken` | Karlo (Original) / pd-codes |
| wowguild_automation | Info / On Work | WoW Guild automation for new members/guests. | `/wow-user`, `/wow-admin`, `/wow-masteradmin` | pd-codes |
| wowtokentracker | Alpha 0.1.0 | Records the WoW Token price over time (retail + optional classic): current price + 24h/7d change + min/max, plus a **dashboard chart widget** of the history. Uses the shared Blizzard key. DE/EN. | `wowtoken`, `wowtokenset region/classic/language/status` | pd-codes |
| wowwatchlist | Alpha 0.1.0 | Track WoW characters and post a **weekly Mythic+ / raid-progress** summary (raider.io). Characters managed in a dashboard table. Opt-in, DE/EN. | `watchlist add/remove/list/post/enable/channel/interval/language` | pd-codes |

> Most cogs support **German & English**: dashboard module texts follow the website language toggle, and each cog has a per-server **language** setting (in its dashboard module) for its Discord output.

## 🌐 Web Dashboard Integration

Some of these cogs feature **native integration with AAA3A's Red-Web-Dashboard** and the
PDC Web Dashboard, so you can manage them from your browser instead of Discord commands:

- **WoWTools** (Guild-profile setup & API config)
- **wowguild_automation** (Full Dashboard based role & channel mapping setup)

The Warcraft Logs cogs share a global **Warcraft Logs API** key panel; the WoW cogs share
the **Blizzard API** key. Configure them once in the dashboard.

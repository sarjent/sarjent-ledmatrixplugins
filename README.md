# Shane Ticker

A LEDMatrix plugin that displays scrolling odds, betting lines, and live scores for upcoming games across multiple sports leagues including NFL, NBA, MLB, NCAA Football, and NCAA Basketball.

> Originally forked from [ChuckBuilds/ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins) `plugins/odds-ticker` (v1.1.0). Substantially rewritten with live game support, bug fixes, and additional sports.

## Changelog

### 1.3.0 (2026-02-23)
- **Feature:** Added per-league `today_only` boolean option. When `true`, only games scheduled for the current calendar day are fetched and displayed for that league, instead of the full `future_fetch_days` lookahead window. Useful for high-volume leagues (NCAA basketball, MLB) where you only want today's slate.

### 1.2.0 (2026-02-23)
- **Fix:** Live games going live during the 1-hour update window were not detected until the next full hourly refresh, causing the ticker to continue showing pre-game odds instead of live scores for up to 55 minutes. A lightweight scoreboard probe now runs every 5 minutes (rate-limited, no display impact) so newly-live games are detected and the refresh rate switches to 60 s within a single probe cycle.
- **Fix:** Live-odds cache keys (`odds_espn_..._live`) were misclassified as `sports_live` (30 s TTL) instead of `odds_live` (120 s TTL) due to the generic `'live'` keyword check running before the `'odds'` check in `cache_strategy.py`. This caused the ESPN odds API to be hit every 30 s per game, frequently triggering the 3-second per-request timeout. *(Fix applied to LEDMatrix core: `src/cache/cache_strategy.py`.)*
- **Fix:** `BaseOddsManager.__init__` was called with `plugin_manager` as the `config_manager` argument, causing a silent `AttributeError` on every startup and forcing the base odds manager to use hardcoded defaults.

## Features

- **Multi-Sport Support**: NFL, NBA, MLB, NCAA Football, NCAA Basketball
- **Scrolling Ticker Display**: Continuous scrolling of odds information
- **Betting Lines**: Point spreads, money lines, and over/under totals
- **Favorite Teams**: Prioritize odds for your favorite teams
- **Broadcast Information**: Show channel logos and game times
- **Configurable Display**: Adjustable scroll speed, duration, and filtering options
- **Background Data Fetching**: Efficient API calls without blocking display

## Configuration

### Global Settings

- `display_duration`: How long to show the ticker (10-300 seconds, default: 30)
- `scroll_speed`: Scrolling speed multiplier (0.5-10, default: 2)
- `scroll_delay`: Delay between scroll steps (0.01-0.5 seconds, default: 0.05)
- `show_favorite_teams_only`: Only show odds for favorite teams (default: false)
- `games_per_favorite_team`: Number of games per favorite team (1-5, default: 1)
- `max_games_per_league`: Maximum games per league (1-20, default: 5)
- `show_odds_only`: Show only odds, no game details (default: false)
- `future_fetch_days`: Days ahead to fetch games (1-14, default: 7)

### Per-League Settings

#### NFL Configuration

```json
{
  "leagues": {
    "nfl": {
      "enabled": true,
      "favorite_teams": ["TB", "DAL", "GB"]
    }
  }
}
```

#### NBA Configuration

```json
{
  "leagues": {
    "nba": {
      "enabled": true,
      "favorite_teams": ["LAL", "BOS", "GSW"]
    }
  }
}
```

#### MLB Configuration

```json
{
  "leagues": {
    "mlb": {
      "enabled": true,
      "favorite_teams": ["NYY", "BOS", "LAD"]
    }
  }
}
```

#### NCAA Football Configuration

```json
{
  "leagues": {
    "ncaa_fb": {
      "enabled": true,
      "favorite_teams": ["UGA", "AUB", "BAMA"]
    }
  }
}
```

#### NCAA Basketball Configuration

```json
{
  "leagues": {
    "ncaam_basketball": {
      "enabled": true,
      "favorite_teams": ["DUKE", "UNC", "KANSAS"]
    }
  }
}
```

## Display Format

The odds ticker displays information in a scrolling format showing:

- **Team Names**: Home and away team abbreviations
- **Point Spread**: Betting line (e.g., "TB -3")
- **Money Line**: Win odds (e.g., "TB -150")
- **Over/Under**: Total points line (e.g., "O/U 45.5")
- **Game Time**: When the game starts
- **Broadcast**: Channel logo and network

## Supported Leagues

The plugin supports the following sports leagues:

- **nfl**: NFL (National Football League)
- **nba**: NBA (National Basketball Association)
- **mlb**: MLB (Major League Baseball)
- **ncaa_fb**: NCAA Football
- **ncaam_basketball**: NCAA Men's Basketball

## Team Abbreviations

### NFL Teams
Common abbreviations: TB, DAL, GB, KC, BUF, SF, PHI, NE, MIA, NYJ, LAC, DEN, LV, CIN, BAL, CLE, PIT, IND, HOU, TEN, JAX, ARI, LAR, SEA, WAS, NYG, MIN, DET, CHI, ATL, CAR, NO

### NBA Teams
Common abbreviations: LAL, BOS, GSW, MIL, PHI, DEN, MIA, BKN, ATL, CHA, NYK, IND, DET, TOR, CHI, CLE, ORL, WAS, HOU, SAS, MIN, POR, SAC, LAC, MEM, DAL, PHX, UTA, OKC, NOP

### MLB Teams
Common abbreviations: NYY, BOS, LAD, HOU, ATL, PHI, TOR, TB, MIL, CHC, CIN, PIT, STL, MIN, CLE, CHW, DET, KC, LAA, OAK, SEA, TEX, ARI, COL, SD, SF, BAL, MIA, NYM, WAS

### NCAA Football Teams
Common abbreviations: UGA, AUB, BAMA, CLEM, OSU, MICH, FSU, LSU, OU, TEX, etc.

### NCAA Basketball Teams
Common abbreviations: DUKE, UNC, KANSAS, KENTUCKY, UCLA, ARIZONA, GONZAGA, BAYLOR, VILLANOVA, MICHIGAN, etc.

## Background Service

The plugin uses background data fetching for efficient API calls:

- Requests timeout after 30 seconds (configurable)
- Up to 3 retries for failed requests
- Priority level 2 (medium priority)
- Updates every hour by default (configurable)

## Data Sources

Odds data is fetched from various sports data APIs and aggregated for display. The plugin integrates with the main LEDMatrix odds management system.

## Dependencies

This plugin requires the main LEDMatrix installation and uses the OddsManager for data access.

## Installation

1. Copy this plugin directory to your `ledmatrix-plugins/plugins/` folder
2. Ensure the plugin is enabled in your LEDMatrix configuration
3. Configure your favorite teams and display preferences
4. Restart LEDMatrix to load the new plugin

## Troubleshooting

- **No odds showing**: Check if leagues are enabled and odds data is available
- **Missing channel logos**: Ensure broadcast logo files exist in your assets/broadcast_logos/ directory
- **Slow scrolling**: Adjust scroll speed and delay settings
- **API errors**: Check your internet connection and data provider availability

## Advanced Features

- **Channel Logos**: Automatically displays broadcast network logos
- **Game Filtering**: Filter by favorite teams or specific criteria
- **Odds Types**: Supports spread, moneyline, and totals
- **Time Display**: Shows game start times and countdown
- **Continuous Loop**: Optionally loop the ticker continuously

## Performance Notes

- The plugin is designed to be lightweight and not impact display performance
- Background fetching ensures smooth scrolling without blocking
- Configurable update intervals balance freshness vs. API load

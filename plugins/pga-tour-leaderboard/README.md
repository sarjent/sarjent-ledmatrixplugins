# PGA Tour Leaderboard Plugin

A custom plugin for the [LEDMatrix project](https://github.com/ryderdamen/LEDMatrix) that displays the top players from the current PGA Tour leaderboard using ESPN data.

## Features

- 📊 Live PGA Tour leaderboard via ESPN's public API — no API key required
- 🏌️ Displays top 10 players (configurable) with position, name, score, and holes completed
- ⛳ Asterisk (`*`) indicator for players currently on the course
- 🏆 PGA Tour logo leads the scroll; automatically switches to the Masters Tournament logo during The Masters
- 🎨 Round status bar color-coded by state: green (live), amber (suspended), blue (round done), grey (final)
- 🤝 Handles team-format events (e.g. Zurich Classic) where players compete as pairs
- 🔙 Falls back to the most recent completed tournament when no event is active (searches up to 30 days back)
- 🖥️ Vegas scroll mode with stacked two-player pair images
- 🗓️ Automatically filters to tournaments within a configurable date range (default: 7 days)
- 🔄 Configurable refresh interval (default: 10 minutes)
- 🎨 User-configurable font size, font style, and colors
- ⭐ Top 3 players highlighted in gold by default

## Requirements

- LEDMatrix project (running on Raspberry Pi)
- Python 3.7+
- Internet connection for ESPN API access

## Installation

### Via the LEDMatrix Plugin Store (recommended)

1. Open the LEDMatrix web interface at `http://your-pi:5000`
2. Navigate to **Plugins → Store**
3. Search for **PGA Tour Leaderboard** and click **Install**

The store handles all dependencies automatically.

### Manual installation

```bash
cd /path/to/LEDMatrix
pip3 install -r plugins/pga-tour-leaderboard/requirements.txt
```

### 4. Configure the plugin

Edit your LEDMatrix configuration (via web UI or `config.yaml`):

```yaml
plugins:
  pga-tour-leaderboard:
    enabled: true
    display_duration: 15
    update_interval: 600  # 10 minutes
    max_players: 10
    fallback_players: 5  # Players to show from previous tournament
    tournament_date_range: 7  # Look ahead 7 days
    font_size: 6
    font_name: "4x6-font.ttf"
    text_color:
      r: 255
      g: 255
      b: 255
    highlight_color:
      r: 255
      g: 215
      b: 0
```

### 5. Restart LEDMatrix

```bash
sudo systemctl restart ledmatrix
```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | boolean | `true` | Enable or disable the plugin |
| `display_duration` | number | `15` | How long to display the leaderboard (seconds) |
| `update_interval` | number | `600` | How often to refresh data from ESPN (seconds, 60-3600) |
| `max_players` | integer | `10` | Maximum number of players to display (1-20) |
| `fallback_players` | integer | `5` | Number of players from previous tournament to show as fallback (1-20) |
| `tournament_date_range` | integer | `7` | Number of days to look ahead for tournaments (0-30) |
| `font_size` | integer | `6` | Font size for text (4-12) |
| `font_name` | string | `"4x6-font.ttf"` | Font file to use (from assets/fonts) |
| `text_color` | object | `{r:255,g:255,b:255}` | RGB color for text (white) |
| `highlight_color` | object | `{r:255,g:215,b:0}` | RGB color for highlighting top 3 (gold) |

### Font Options

Available fonts (from LEDMatrix assets/fonts):
- `4x6-font.ttf` - Small, compact font (recommended for 64x32 displays)
- `PressStart2P-Regular.ttf` - Retro pixel font
- `tom-thumb.ttf` - Tiny font for maximum information density

### Color Customization

Colors are specified as RGB values (0-255):

```yaml
# White text
text_color:
  r: 255
  g: 255
  b: 255

# Gold highlight for leaders
highlight_color:
  r: 255
  g: 215
  b: 0

# Other color examples:
# Red: {r: 255, g: 0, b: 0}
# Green: {r: 0, g: 255, b: 0}
# Blue: {r: 0, g: 0, b: 255}
# Yellow: {r: 255, g: 255, b: 0}
```

## How It Works

1. **Data Fetching**: The plugin fetches PGA Tour leaderboard data from ESPN's public API
2. **Tournament Filtering**: It automatically filters to show only tournaments within your configured date range (e.g., today + 7 days)
3. **Fallback Mode**: If no current tournament is found, the plugin automatically searches for the most recent completed tournament (looks back up to 30 days) and displays the top finishers
4. **Leaderboard Display**: Shows player position, name, and score scrolling horizontally right-to-left
   - PGA Tour logo appears at the beginning of the scroll
   - Tournament name followed by all players separated by " | "
   - When showing a previous tournament, displays "PREV:" before the tournament name
5. **Highlighting**: The tournament name and top 3 players are displayed in the highlight color (gold by default)
6. **Scrolling**: Smooth horizontal scrolling animation at 120 FPS for readability
7. **Caching**: API responses are cached to respect the update interval and minimize requests

## Display Format

The display is split into two sections:
- **Top section (24 pixels)**: PGA logo + player standings scroll horizontally right-to-left
- **Bottom section (8 pixels)**: Tournament name remains static (centered)

**Current Tournament Display:**
```
Top (scrolling):    [🏌️] 1. *J.Smith -5 (12) | 2. A.Jones -4 (F) | 3. *B.Lee -3 (15) | ... →
Bottom (static):              The Genesis Invitational
```

**Previous Tournament Fallback:**
```
Top (scrolling):    [🏌️] 1. J.Smith -12 (F) | 2. A.Jones -10 (F) | 3. B.Lee -8 (F) | ... →
Bottom (static):            PREV: The American Express
```

**Display Elements:**
- **Top section**: Scrolling content
  - **PGA Tour logo** leads the scroll (up to 36px wide, sized to fill scroll area)
  - **Asterisk (*)** prefix indicates player is currently on the course
  - **Holes completed** shown in parentheses: (12) = through 12 holes, (F) = finished round
  - **Top 3 players** highlighted in gold
  - **Remaining players** in white
  - **Smooth scrolling** animation at 120 FPS
  - **Separator** " | " between entries

- **Bottom section**: Static tournament bar
  - **Tournament name** centered and in gold/highlight color
  - **"PREV:"** prefix for previous tournaments
  - **Auto-truncated** if name is too long to fit display

**Examples:**
- `1. *S.Scheffler -8 (14)` - Scottie Scheffler in 1st place, 8 under par, currently playing hole 14
- `2. R.McIlroy -7 (F)` - Rory McIlroy in 2nd place, 7 under par, finished the round
- `3. *J.Thomas -6 (9)` - Justin Thomas in 3rd place, 6 under par, currently playing hole 9

## Troubleshooting

### Plugin not showing up
- Check that the plugin is enabled in the configuration
- Verify the plugin is loaded: Check the LEDMatrix logs at `/var/log/ledmatrix/ledmatrix.log`
- Ensure the manifest.json is valid JSON

### No tournament data displayed
- Check that there's a PGA Tour event within your configured date range
- Verify internet connectivity: `ping site.api.espn.com`
- Check the update interval - data may be cached
- View logs for API errors

### Display issues
- Try a smaller font size if text is cut off
- Adjust `max_players` if not all players fit on screen
- Check display dimensions match your LED matrix size

### Viewing logs

```bash
# View LEDMatrix logs
tail -f /var/log/ledmatrix/ledmatrix.log | grep pga-tour

# Or via journalctl
sudo journalctl -u ledmatrix -f | grep pga-tour
```

## ESPN API Information

This plugin uses ESPN's public API endpoint:
```
https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard
```

**Note**: This is an unofficial API. ESPN does not officially support or document this endpoint, and it may change without notice. For production use or guaranteed reliability, consider using an official golf API service like [SportsDataIO](https://sportsdata.io/) or [Sportradar](https://sportradar.com/).

**Update v1.1.1**: Changed from `/leaderboard` to `/scoreboard` endpoint as the leaderboard endpoint was returning 404 errors.

## PGA Tour Logo

The plugin displays the PGA Tour logo at the beginning of the scrolling leaderboard.

**Logo File Location:**
```
assets/sports/pga_logos/pga_logo.png
```

**Setup Instructions:**
1. Download or create a PGA Tour logo (PNG format, 20x28px recommended)
2. Create the directory on your Raspberry Pi:
   ```bash
   mkdir -p /path/to/LEDMatrix/assets/sports/pga_logos
   ```
3. Upload the logo file:
   ```bash
   scp pga_logo.png pi@your-pi-ip:/path/to/LEDMatrix/assets/sports/pga_logos/
   ```
4. Set permissions:
   ```bash
   chmod 644 /path/to/LEDMatrix/assets/sports/pga_logos/pga_logo.png
   ```

**Note**: The logos are bundled with the plugin and installed automatically on first run. If the logo file is not found the plugin logs a warning and displays the leaderboard without it.

If the logo file is not found, the plugin will log a warning and display the leaderboard without the logo.

## Development

### Project Structure

```
plugins/pga-tour-leaderboard/
├── manifest.json           # Plugin metadata and entry point
├── config_schema.json      # Configuration schema for web UI
├── manager.py              # Main plugin implementation
├── requirements.txt        # Python dependencies
├── README.md               # This file
├── pga-logo.png            # PGA Tour logo (auto-installed to assets/)
└── masters-logo.png        # Masters Tournament logo (auto-installed to assets/)
```

### Testing

To test the plugin locally:

```python
# Test ESPN API connection
import requests
response = requests.get('https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard')
print(response.json())
```

### Contributing

1. Fork this repository
2. Create a feature branch
3. Make your changes
4. Test on your LED matrix
5. Submit a pull request

## License

This plugin is provided as-is for use with the LEDMatrix project. Please respect ESPN's terms of service when using their API.

## Credits

- Built for the [LEDMatrix project](https://github.com/ryderdamen/LEDMatrix)
- Data provided by ESPN's public API

## Support

For issues specific to this plugin, please open an issue in this repository.
For LEDMatrix issues, see the [LEDMatrix documentation](https://github.com/ryderdamen/LEDMatrix).

If this plugin is useful to you, consider buying me a coffee!

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-sarjent-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/sarjent)

---

**Enjoy tracking your favorite PGA Tour players on your LED matrix! ⛳**

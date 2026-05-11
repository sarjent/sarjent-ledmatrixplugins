# ESPN PGA Tour API Fields Reference

This document describes the fields available from the ESPN PGA Tour Scoreboard API.

## API Endpoint

```
https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard
```

### Optional Parameters
- `dates`: Specific date in YYYYMMDD format (e.g., `20240115`)

## Response Structure

### Top Level
```json
{
  "events": [],      // Array of tournament events
  "season": {},      // Season information
  "week": {},        // Week information
  "leagues": []      // League information
}
```

## Event Object (Tournament)

### Currently Used Fields
```python
event = {
    "name": "The Genesis Invitational",        # Tournament name
    "date": "2024-02-15T18:00Z",              # Tournament start date (ISO format)
    "status": {                                # Tournament status
        "type": {
            "state": "pre",                    # Status: "pre", "in", "post"
            "completed": false,
            "description": "Scheduled",
            "detail": "Thu, February 15"
        }
    },
    "competitions": []                         # Array of competition data (see below)
}
```

### Additional Available Fields
```python
event = {
    "id": "401465534",                        # Event ID
    "uid": "s:20~l:10~e:401465534",          # Unique identifier
    "shortName": "Genesis",                   # Short name
    "venue": {                                # Course information
        "id": "820",
        "fullName": "Riviera Country Club",
        "address": {
            "city": "Pacific Palisades",
            "state": "CA"
        }
    },
    "season": {                               # Season info
        "year": 2024,
        "type": 2
    },
    "weather": {                              # Weather conditions (if available)
        "displayValue": "Sunny",
        "temperature": 72,
        "highTemperature": 75
    }
}
```

## Competition Object

### Currently Used Fields
```python
competition = {
    "competitors": []                          # Array of player/competitor data
}
```

### Additional Available Fields
```python
competition = {
    "id": "401465534",
    "date": "2024-02-15T18:00Z",
    "attendance": 0,
    "competitors": [],
    "purse": 20000000,                        # Prize money in dollars
    "displayClock": "0:00",
    "broadcast": [],                          # TV broadcast info
    "leaders": []                             # Current leaders (if available)
}
```

## Competitor Object (Player)

### Currently Used Fields
```python
competitor = {
    "sortOrder": 1,                           # Position/rank (used for sorting)
    "status": "active",                       # Player status
    "athlete": {                              # Player information
        "displayName": "Scottie Scheffler",
        "shortName": "S. Scheffler"
    },
    "statistics": [                           # Player statistics
        {
            "name": "score",
            "displayValue": "-12",            # Score relative to par
            "value": -12
        }
    ]
}
```

### Additional Available Fields
```python
competitor = {
    "id": "9478",
    "uid": "s:20~l:10~a:9478",
    "type": "athlete",
    "order": 1,
    "homeAway": "home",
    "athlete": {
        "id": "9478",
        "uid": "s:20~l:10~a:9478",
        "displayName": "Scottie Scheffler",
        "shortName": "S. Scheffler",
        "links": [],
        "headshot": "https://...",             # Player photo URL
        "jersey": "1",
        "position": {
            "name": "Golfer",
            "displayName": "Golfer"
        },
        "flag": {                              # Country flag
            "href": "https://...",
            "alt": "United States",
            "rel": ["country-flag"]
        }
    },
    "score": "-12",                           # Alternative score field
    "linescores": [                           # Round-by-round scores
        {
            "value": 68,
            "displayValue": "68"
        },
        {
            "value": 67,
            "displayValue": "67"
        }
    ],
    "statistics": [
        {
            "name": "score",
            "displayValue": "-12",
            "value": -12
        },
        {
            "name": "thru",                   # Holes completed
            "displayValue": "F",              # "F" = finished, or hole number
            "value": 18
        },
        {
            "name": "teeTime",
            "displayValue": "9:15 AM",
            "value": "2024-02-15T17:15Z"
        },
        {
            "name": "strokes",                # Total strokes
            "displayValue": "276",
            "value": 276
        },
        {
            "name": "Eagles",
            "displayValue": "2",
            "value": 2
        },
        {
            "name": "Birdies",
            "displayValue": "18",
            "value": 18
        },
        {
            "name": "Pars",
            "displayValue": "42",
            "value": 42
        },
        {
            "name": "Bogeys",
            "displayValue": "6",
            "value": 6
        }
    ]
}
```

## Statistics Available

The `statistics` array can contain various stats depending on tournament status:

- `score`: Score relative to par (e.g., "-12", "E", "+3")
- `thru`: Holes completed ("F" for finished, or "12" for through 12 holes)
- `teeTime`: Scheduled tee time
- `strokes`: Total strokes taken
- `Eagles`: Number of eagles
- `Birdies`: Number of birdies
- `Pars`: Number of pars
- `Bogeys`: Number of bogeys
- `DoubleBogeys`: Number of double bogeys or worse

## Status States

Tournament status can be:
- `pre`: Scheduled/Not started
- `in`: In progress
- `post`: Completed

## Notes

1. **Score Format**: Scores are displayed as:
   - Negative numbers for under par (e.g., "-12")
   - "E" for even par
   - Positive numbers for over par (e.g., "+3")

2. **Sort Order**: The `sortOrder` field represents the player's position:
   - Lower numbers = better position
   - Ties have the same `sortOrder` value

3. **Data Availability**: Some fields (like weather, broadcast, detailed stats) may not always be available depending on the tournament status and ESPN's data feed.

4. **Caching**: The API responses should be cached to minimize requests. The current plugin caches for:
   - Current tournament: 10 minutes (configurable)
   - Previous tournament: 24 hours

## Example Usage in Plugin

### Getting Player Score
```python
stats = competitor.get('statistics', [])
for stat in stats:
    if stat.get('name') == 'score':
        score = stat.get('displayValue', 'E')
```

### Getting Player Name
```python
athlete = competitor.get('athlete', {})
full_name = athlete.get('displayName', 'Unknown')
short_name = athlete.get('shortName', full_name)
```

### Checking Tournament Status
```python
status = event.get('status', {}).get('type', {}).get('state', '')
is_completed = (status == 'post')
is_in_progress = (status == 'in')
is_upcoming = (status == 'pre')
```

### Getting Tournament Date
```python
from datetime import datetime
date_str = event.get('date')
event_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
```

## Potential Plugin Enhancements

Based on available fields, you could add:

1. **Holes Completed**: Show "Thru 12" or "F" for finished
2. **Tee Times**: Display when players are scheduled to tee off
3. **Round-by-Round Scores**: Show individual round scores
4. **Country Flags**: Display player nationality
5. **Player Photos**: Show headshots (if display supports it)
6. **Tournament Details**: Show course name, purse, location
7. **Detailed Stats**: Show eagles, birdies, pars, bogeys
8. **Weather**: Display current weather conditions
9. **Live Updates**: Show which players are currently on the course
10. **Prize Money**: Display tournament purse

## Testing

Use the included `test_api.py` script to explore the current API response:

```bash
python3 test_api.py
```

This will show:
- Current tournament data with top 5 players
- Previous tournament fallback data
- Complete API response structure

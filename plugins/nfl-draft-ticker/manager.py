"""
NFL Draft Plugin for LEDMatrix

Displays projected and live NFL draft picks from ESPN API.
Supports dual-mode operation: projections (off-season) and live tracking (during draft).

Features:
- Projected draft picks from ESPN (mock draft data)
- Live draft tracking during the NFL Draft event
- Automatic mode switching between projections and live
- Configurable rounds, fonts, colors
- Smooth horizontal scrolling through picks
- Team logos displayed alongside player names
"""

import concurrent.futures
import html
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError
import json

from PIL import Image, ImageDraw, ImageFont

from src.plugin_system.base_plugin import BasePlugin, VegasDisplayMode
from src.common.scroll_helper import ScrollHelper
from src.common.logo_helper import LogoHelper
from src.common.api_helper import APIHelper

logger = logging.getLogger(__name__)


class NFLDraftPlugin(BasePlugin):
    """
    NFL Draft plugin that displays projected and live draft picks.

    Features:
    - Projected draft picks from ESPN (mock draft data)
    - Live draft tracking during the NFL Draft event
    - Automatic mode switching between projections and live
    - Configurable rounds, fonts, colors
    - Smooth horizontal scrolling through picks
    - Team logos displayed alongside player names
    """

    # ESPN API Endpoints
    # Site API provides draft status and actual results (live/post-draft)
    ESPN_DRAFT_SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/draft"
    # Core API provides detailed athlete data and actual draft results (post-draft)
    ESPN_DRAFT_CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/{year}/draft"
    ESPN_DRAFT_ATHLETES = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/{year}/draft/athletes"

    # Tankathon mock draft (pre-draft only)
    TANKATHON_MOCK_DRAFT = "https://www.tankathon.com/nfl/mock_draft"

    def __init__(self, plugin_id: str, config: Dict[str, Any],
                 display_manager, cache_manager, plugin_manager):
        """Initialize the NFL Draft plugin."""
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        # Display dimensions
        self.display_width = display_manager.matrix.width
        self.display_height = display_manager.matrix.height

        # Initialize helpers
        self.scroll_helper = ScrollHelper(self.display_width, self.display_height, self.logger)
        self.logo_helper = LogoHelper(self.display_width, self.display_height, logger=self.logger)
        self.api_helper = APIHelper(cache_manager, logger=self.logger)

        # Load configuration
        self._load_config()

        # Data storage — draft mode
        self.draft_picks: List[Dict[str, Any]] = []
        self.is_draft_live = False
        self.draft_status = "unknown"  # "pre", "live", "complete"
        self.current_round = 1
        self.last_update_time: Optional[float] = None
        self.last_live_check_time: Optional[float] = None
        self._state_lock = threading.Lock()

        # Data storage — leaders / injuries modes
        self.leaders_data: List[Dict[str, Any]] = []
        self.injuries_data: List[Dict[str, Any]] = []
        self.last_leaders_update: Optional[float] = None
        self.last_injuries_update: Optional[float] = None

        # Font loading - separate sizes for player name vs details
        self.player_name_font = self._load_font(self.player_name_font_size)
        self.detail_font = self._load_font(self.detail_font_size)
        self.injury_detail_font = self._load_font(self.injury_detail_font_size)

        # Logo path (using core LEDMatrix assets)
        self.logo_base_path = Path("assets/sports/nfl_logos")

        # Ensure NFL Draft logo is installed and load it for the scroll header
        self._ensure_logo_installed()
        self.nfl_draft_logo = self._load_nfl_draft_logo()

        self.logger.info(f"NFL Draft plugin initialized for year {self.draft_year}")

        # Kick off the initial data fetch in the background so __init__ returns
        # immediately. display() calls _display_no_data() until draft_picks is
        # populated (the existing `if not self.draft_picks` guard handles this).
        threading.Thread(target=self.update, daemon=True).start()

    def _load_config(self) -> None:
        """Load and parse configuration values."""
        # Font settings
        self.font_name = self.config.get("font", "PressStart2P-Regular.ttf")
        self.player_name_font_size = self.config.get("player_name_font_size", 12)
        self.detail_font_size = self.config.get("detail_font_size", 8)
        self.injury_detail_font_size = self.config.get("injury_detail_font_size", 7)

        # Color settings
        player_color = self.config.get("player_name_color", {"r": 255, "g": 255, "b": 255})
        self.player_color = (
            player_color.get("r", 255),
            player_color.get("g", 255),
            player_color.get("b", 255)
        )

        pick_color = self.config.get("pick_number_color", {"r": 255, "g": 255, "b": 255})
        self.pick_color = (
            pick_color.get("r", 255),
            pick_color.get("g", 255),
            pick_color.get("b", 255)
        )

        # Scroll settings
        self.scroll_speed = self.config.get("scroll_speed", 30)
        self.scroll_helper.set_scroll_speed(self.scroll_speed)

        # Refresh intervals
        self.live_refresh_interval = self.config.get("live_refresh_interval", 600)  # 10 minutes
        self.projection_refresh_interval = self.config.get("projection_refresh_interval", 86400)  # 24 hours

        # Display settings
        self.show_position = self.config.get("show_position", True)
        self.show_college = self.config.get("show_college", True)
        self.item_gap = self.config.get("item_gap", 32)

        # Logo size - 0 means auto-size based on display height (like NFL Scoreboard)
        logo_size_config = self.config.get("logo_size", 0)
        if logo_size_config == 0:
            # Auto-size: use display height (fills vertical space)
            self.logo_size = self.display_height
        else:
            self.logo_size = logo_size_config

        # Dynamic duration settings
        dynamic_duration = self.config.get("dynamic_duration", {})
        self.dynamic_duration_enabled = dynamic_duration.get("enabled", True)
        self.min_duration = dynamic_duration.get("min_duration", 30)
        self.max_duration = dynamic_duration.get("max_duration", 300)

        # Configure scroll helper dynamic duration
        self.scroll_helper.set_dynamic_duration_settings(
            enabled=self.dynamic_duration_enabled,
            min_duration=self.min_duration,
            max_duration=self.max_duration,
            buffer=0.1
        )

        # Draft year (0 = auto-detect current/upcoming)
        self.draft_year = self.config.get("draft_year", 0)
        if self.draft_year == 0:
            self.draft_year = self._get_current_draft_year()

        # Simulation settings — override draft_year when active
        self.simulate_live = self.config.get("simulate_live", False)
        self.simulate_year = self.config.get("simulate_year", 2025)
        if self.simulate_live:
            self.draft_year = self.simulate_year

        # Favorite teams for live-mode highlights (up to 3 abbreviations)
        fav_raw = self.config.get("favorite_teams", [])
        if isinstance(fav_raw, list):
            self.favorite_teams = [str(t).upper().strip() for t in fav_raw if t][:3]
        else:
            self.favorite_teams = []

        # Post-draft display settings
        self.display_rounds = self.config.get("display_rounds", 3)
        self.post_draft_days = self.config.get("post_draft_days", 7)
        self.post_draft_show = self.config.get("post_draft_show", "both")

        # Leaders mode settings
        _stat_map = {"passing": "passingYards", "rushing": "rushingYards", "receiving": "receivingYards"}
        _raw_types = self.config.get("leaders_stat_types", ["passing", "rushing", "receiving"])
        self.leaders_stat_types = [_stat_map[t] for t in _raw_types if t in _stat_map]
        self.leaders_refresh_interval = self.config.get("leaders_refresh_interval", 3600)

        # Injuries mode settings
        self.injury_positions = self.config.get("injury_positions", ["QB", "RB", "WR", "TE", "K"])
        self.injury_statuses = self.config.get("injury_statuses", ["Out", "Doubtful", "Injured Reserve"])
        self.show_ota_active = self.config.get("show_ota_active", False)
        self.injuries_refresh_interval = self.config.get("injuries_refresh_interval", 3600)

    def _load_font(self, size: int) -> ImageFont.ImageFont:
        """Load configured font at specified size."""
        try:
            font_path = Path("assets/fonts") / self.font_name
            if font_path.exists():
                return ImageFont.truetype(str(font_path), size)
        except Exception as e:
            self.logger.warning(f"Could not load font {self.font_name} at size {size}: {e}")

        return ImageFont.load_default()

    def _get_current_draft_year(self) -> int:
        """Determine the current/upcoming draft year."""
        now = datetime.now()
        year = now.year
        # Stay on the current year's draft until its post-draft window has closed.
        # Using a hard month cutoff (< 5) would flip to next year on May 1 — before
        # the window expires — causing _is_post_draft_window() to compute dates for
        # the wrong year. Instead, compute the actual window end and advance only
        # after it has passed.
        post_draft_days = self.config.get("post_draft_days", 7)
        last_april = datetime(year, 4, 30)
        days_back = (last_april.weekday() - 5) % 7
        draft_end = (last_april - timedelta(days=days_back)).replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
        if now > draft_end + timedelta(days=post_draft_days):
            return year + 1
        return year

    def _fetch_draft_data(self) -> Dict[str, Any]:
        """
        Fetch draft data from ESPN site API.

        This endpoint provides mock draft picks with team projections (pre-draft)
        or actual draft results (post-draft).
        """
        cache_key = f"nfl_draft_site_{self.draft_year}"
        # Use live_refresh_interval during draft week regardless of current
        # is_draft_live state so the live transition is detected within 10 min.
        cache_ttl = (
            self.live_refresh_interval
            if (self.is_draft_live or self._is_draft_date())
            else self.projection_refresh_interval
        )

        data = self.api_helper.get(
            self.ESPN_DRAFT_SITE,
            cache_key=cache_key,
            cache_ttl=cache_ttl
        )
        return data or {}

    def _fetch_all_prospects(self) -> List[Dict[str, Any]]:
        """
        Fetch all draft prospects from ESPN core API.

        This fetches the full list of draft-eligible athletes and their rankings,
        allowing us to build a complete mock draft by matching prospects to picks.

        Returns:
            List of prospect dictionaries sorted by overall rank
        """
        cache_key = f"nfl_draft_prospects_{self.draft_year}"

        # Check cache first
        cached_data = self.cache_manager.get(cache_key)
        if cached_data:
            self.logger.debug("Using cached prospect data")
            return cached_data

        prospects = []

        try:
            # Get list of all draft athletes
            athletes_url = self.ESPN_DRAFT_ATHLETES.format(year=self.draft_year)
            athletes_url += "?limit=300"  # Get up to 300 prospects

            self.logger.info(f"Fetching draft athletes list from {athletes_url}")

            req = Request(athletes_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode())

            items = data.get("items", [])
            self.logger.info(f"Found {len(items)} athlete references")

            if not items:
                return prospects

            # Extract athlete URLs from references
            athlete_urls = []
            for item in items:
                url = item.get("$ref")
                if url:
                    athlete_urls.append(url)

            # Fetch athlete details in parallel (limit concurrency)
            def fetch_athlete(url: str) -> Optional[Dict[str, Any]]:
                try:
                    req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urlopen(req, timeout=10) as response:
                        return json.loads(response.read().decode())
                except Exception as e:
                    self.logger.debug(f"Failed to fetch athlete: {e}")
                    return None

            # Use ThreadPoolExecutor for parallel fetching
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                athlete_data = list(executor.map(fetch_athlete, athlete_urls[:64]))  # Limit to top 64

            # Process athlete data
            for athlete in athlete_data:
                if not athlete:
                    continue

                # Extract overall rank from attributes
                overall_rank = 999
                for attr in athlete.get("attributes", []):
                    if attr.get("name") == "overall":
                        try:
                            overall_rank = int(float(attr.get("value", 999)))
                        except (ValueError, TypeError):
                            pass

                # Get position
                position = athlete.get("position", {})
                pos_abbr = position.get("abbreviation", "") if isinstance(position, dict) else ""

                # Get college team
                college = ""
                college_team = athlete.get("team", {})
                if college_team:
                    college = college_team.get("shortDisplayName", college_team.get("name", ""))

                prospect = {
                    "id": athlete.get("id"),
                    "displayName": athlete.get("displayName", "Unknown"),
                    "position": pos_abbr,
                    "college": college,
                    "overall_rank": overall_rank
                }
                prospects.append(prospect)

            # Sort by overall rank
            prospects.sort(key=lambda x: x.get("overall_rank", 999))

            self.logger.info(f"Fetched and ranked {len(prospects)} prospects")

            # Cache the results
            if prospects:
                self.cache_manager.set(cache_key, prospects, ttl=self.projection_refresh_interval)

        except Exception as e:
            self.logger.error(f"Error fetching prospects: {e}", exc_info=True)

        return prospects

    def _fetch_tankathon_mock_draft(self) -> List[Dict[str, Any]]:
        """
        Fetch pre-draft mock picks from Tankathon.

        Scrapes https://www.tankathon.com/nfl/mock_draft and returns a list of
        pick dicts in the same format as _fetch_draft_picks().
        """
        cache_key = f"tankathon_mock_draft_{self.draft_year}"
        cached = self.cache_manager.get(cache_key)
        if cached:
            self.logger.debug("Using cached Tankathon mock draft data")
            return cached

        picks = []
        try:
            req = Request(
                self.TANKATHON_MOCK_DRAFT,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.tankathon.com/",
                    "Connection": "keep-alive",
                }
            )
            with urlopen(req, timeout=30) as response:
                page = response.read().decode("utf-8", errors="replace")

            round_label_pattern = re.compile(r'mock-round-label nfl[^>]*>Round (\d+)<')
            row_pattern = re.compile(
                r'<div class="mock-row nfl">'
                r'<div class="mock-row-pick-number">(\d+)</div>'
                r'.*?alt="([^"]*)"'
                r'.*?<div class="mock-row-name">([^<]+)</div>'
                r'.*?<div class="mock-row-school-position">([^<]+)</div>',
                re.DOTALL
            )

            round_starts = [(m.start(), int(m.group(1))) for m in round_label_pattern.finditer(page)]

            round_counters: Dict[int, int] = {}
            current_round = 1

            for m in row_pattern.finditer(page):
                # Determine round: find the last round label before this pick
                for rs_pos, rs_round in reversed(round_starts):
                    if rs_pos < m.start():
                        current_round = rs_round
                        break

                pick_number = int(m.group(1))
                team_abbr = m.group(2).strip().upper()
                player_name = html.unescape(m.group(3).strip())
                school_pos = html.unescape(m.group(4).strip())

                parts = school_pos.split("|")
                position = parts[0].strip() if parts else ""
                college = parts[1].strip() if len(parts) > 1 else ""

                round_counters[current_round] = round_counters.get(current_round, 0) + 1

                picks.append({
                    "pick_number": pick_number,
                    "round": current_round,
                    "round_pick": round_counters[current_round],
                    "team_abbr": team_abbr,
                    "team_name": "",
                    "player_name": player_name,
                    "position": position,
                    "college": college,
                })

            self.logger.info(f"Fetched {len(picks)} Tankathon mock draft picks")
            if picks:
                self.cache_manager.set(cache_key, picks, ttl=self.projection_refresh_interval)

        except Exception as e:
            self.logger.error(f"Error fetching Tankathon mock draft: {e}", exc_info=True)

        if not picks:
            picks = self._fetch_espn_predraft_order()

        return picks

    def _fetch_espn_predraft_order(self) -> List[Dict[str, Any]]:
        """
        Fallback: build a Round 1 pick list from ESPN's pre-draft order.

        ESPN pre-draft picks have team assignments but no player names yet.
        Used when Tankathon is unreachable so the display shows the draft
        order rather than nothing.
        """
        picks = []
        try:
            data = self._fetch_draft_data()
            teams_lookup = {str(t.get("id")): t for t in data.get("teams", [])}
            round1 = [p for p in data.get("picks", []) if p.get("round") == 1]
            for raw in round1:
                team_id = str(raw.get("teamId", ""))
                team_info = teams_lookup.get(team_id, {})
                picks.append({
                    "pick_number": raw.get("overall", 0),
                    "round": 1,
                    "round_pick": raw.get("pick", 0),
                    "team_abbr": team_info.get("abbreviation", ""),
                    "team_name": team_info.get("displayName", ""),
                    "player_name": "TBD",
                    "position": "",
                    "college": "",
                })
            if picks:
                self.logger.info(f"ESPN pre-draft fallback: {len(picks)} Round 1 picks (player names TBD)")
        except Exception as e:
            self.logger.error(f"Error fetching ESPN pre-draft fallback: {e}")
        return picks

    def _fetch_nfl_teams(self) -> Dict[str, str]:
        """
        Fetch NFL team ID → abbreviation mapping from ESPN site API.

        Returns:
            Dict mapping team ID string to team abbreviation (e.g. {'10': 'KC'})
        """
        cache_key = "nfl_teams_lookup"
        cached = self.cache_manager.get(cache_key)
        if cached:
            return cached

        teams: Dict[str, str] = {}
        try:
            url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams?limit=50"
            req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode())

            # Response: {"sports": [{"leagues": [{"teams": [...]}]}]}
            for entry in (data.get("sports", [{}])[0]
                          .get("leagues", [{}])[0]
                          .get("teams", [])):
                team = entry.get("team", {})
                team_id = str(team.get("id", ""))
                abbr = team.get("abbreviation", "")
                if team_id and abbr:
                    teams[team_id] = abbr

            self.logger.info(f"Fetched {len(teams)} NFL team abbreviations")
            if teams:
                self.cache_manager.set(cache_key, teams, ttl=86400)

        except Exception as e:
            self.logger.error(f"Error fetching NFL teams: {e}")

        return teams

    def _fetch_historical_picks(self) -> List[Dict[str, Any]]:
        """
        Fetch completed draft picks from ESPN core API for simulate_live mode.

        The core API /draft/rounds endpoint returns all round objects inline in
        the items array — each item contains its full picks list directly.
        Athlete $ref URLs are resolved in parallel for player name and position.

        Returns:
            List of pick dicts in the same format as _fetch_draft_picks()
        """
        year = self.simulate_year
        cache_key = f"nfl_draft_historical_{year}"
        cached = self.cache_manager.get(cache_key)
        if cached:
            self.logger.debug(f"Using cached historical picks for {year}")
            return cached

        teams_lookup = self._fetch_nfl_teams()
        picks: List[Dict[str, Any]] = []

        try:
            # Picks are inline in the rounds list — each item is a full round object
            rounds_url = (
                f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
                f"/seasons/{year}/draft/rounds?lang=en&region=us&limit=10"
            )
            req = Request(rounds_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=15) as response:
                rounds_data = json.loads(response.read().decode())

            # Collect picks from all rounds in the response
            raw_picks: List[Tuple[int, Dict]] = []
            for item in rounds_data.get("items", []):
                round_num = item.get("number", 0)
                for pick in item.get("picks", []):
                    raw_picks.append((round_num, pick))

            self.logger.info(f"Found {len(raw_picks)} picks across configured rounds for {year} draft")

            # Resolve athlete $ref URLs in parallel (index-aligned with raw_picks)
            athlete_urls = [
                pick.get("athlete", {}).get("$ref", "") for (_rn, pick) in raw_picks
            ]

            def fetch_athlete_ref(url: str) -> Optional[Dict]:
                if not url:
                    return None
                try:
                    req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urlopen(req, timeout=10) as response:
                        return json.loads(response.read().decode())
                except Exception as e:
                    self.logger.debug(f"Failed to fetch athlete ref: {e}")
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                athlete_results = list(executor.map(fetch_athlete_ref, athlete_urls))

            # Build standardised pick dicts
            for i, (round_num, raw_pick) in enumerate(raw_picks):
                athlete = athlete_results[i]

                # Extract team ID from $ref URL — strip query params before splitting
                team_ref = raw_pick.get("team", {}).get("$ref", "")
                team_id = team_ref.split("?")[0].rstrip("/").split("/")[-1] if team_ref else ""
                team_abbr = teams_lookup.get(team_id, "")

                player_name = "TBD"
                position = ""
                if athlete:
                    player_name = athlete.get("displayName", "TBD")
                    pos = athlete.get("position", {})
                    if isinstance(pos, dict):
                        position = pos.get("abbreviation", "")
                    # Note: athlete.college and athlete.team are $ref objects in the
                    # draft API; college name is not available without extra fetches.

                pick_data: Dict[str, Any] = {
                    "pick_number": raw_pick.get("overall", i + 1),
                    "round": round_num,
                    "round_pick": raw_pick.get("pick", 0),
                    "team_abbr": team_abbr,
                    "team_name": "",
                    "player_name": player_name,
                    "position": position,
                    "college": "",
                }

                if pick_data["team_abbr"] or pick_data["player_name"] != "TBD":
                    picks.append(pick_data)

            picks.sort(key=lambda x: x.get("pick_number", 0))
            self.logger.info(f"Fetched {len(picks)} historical picks for {year}")

            if picks:
                self.cache_manager.set(cache_key, picks, ttl=86400)

        except Exception as e:
            self.logger.error(f"Error fetching historical picks: {e}", exc_info=True)

        return picks

    def _fetch_draft_picks(self, round_num: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetch draft picks from ESPN site API.

        For pre-draft: Builds mock draft by matching top prospects with draft order.
        For live/post-draft: Uses actual draft pick data.

        Args:
            round_num: Specific round to fetch, or None for all configured rounds

        Returns:
            List of draft pick dictionaries
        """
        picks = []

        data = self._fetch_draft_data()

        # Update draft status from ESPN if we got data
        if data:
            status = data.get("status", {})
            if status:
                state = status.get("state", "").lower()
                if state == "in":
                    self.draft_status = "live"
                    self.is_draft_live = True
                elif state == "post":
                    self.draft_status = "complete"
                    self.is_draft_live = False
                else:
                    self.draft_status = "pre"
                    self.is_draft_live = False

                # Get current round from status; clamp to >=1 so downstream
                # functions (_get_display_round, on-the-clock logic) never see 0.
                current_round = status.get("round", 1)
                if isinstance(current_round, int):
                    self.current_round = max(1, current_round)

        # If ESPN returned nothing or gave no status, assume pre-draft
        if self.draft_status == "unknown":
            self.logger.info("No ESPN draft status — defaulting to pre-draft mode")
            self.draft_status = "pre"
            self.is_draft_live = False

        # For pre-draft, use Tankathon mock draft directly — no ESPN data needed
        if self.draft_status == "pre":
            tankathon_picks = self._fetch_tankathon_mock_draft()
            if round_num is not None:
                return [p for p in tankathon_picks if p["round"] == round_num]
            return tankathon_picks

        if not data:
            self.logger.warning("No draft data returned from ESPN API")
            return picks

        # Build team lookup (teamId -> team info)
        teams_lookup = {}
        for team in data.get("teams", []):
            team_id = team.get("id")
            if team_id:
                teams_lookup[str(team_id)] = team

        # Get draft order from picks
        raw_picks = data.get("picks", [])
        self.logger.info(f"Found {len(raw_picks)} picks in ESPN response")

        # For live/post-draft, build picks list from ESPN actual data
        for idx, raw_pick in enumerate(raw_picks):
            pick_number = raw_pick.get("overall", idx + 1)
            pick_round = raw_pick.get("round", 1)

            if round_num is not None and pick_round != round_num:
                continue

            team_id = str(raw_pick.get("teamId", ""))
            team_info = teams_lookup.get(team_id, {})

            pick_data = {
                "pick_number": pick_number,
                "round": pick_round,
                "round_pick": raw_pick.get("pick", 0),
                "team_abbr": team_info.get("abbreviation", ""),
                "team_name": team_info.get("displayName", ""),
                "player_name": "TBD",
                "position": "",
                "college": ""
            }

            if raw_pick.get("athlete"):
                athlete = raw_pick["athlete"]
                pick_data["player_name"] = athlete.get("displayName", "TBD")
                pick_data["_athlete_id"] = str(athlete.get("id", ""))
                position = athlete.get("position", {})
                if isinstance(position, dict):
                    pick_data["position"] = position.get("abbreviation", "")
                college_team = athlete.get("team", {})
                if college_team and isinstance(college_team, dict):
                    pick_data["college"] = college_team.get("shortDisplayName", college_team.get("name", ""))

            if pick_data["team_abbr"] or pick_data["player_name"] != "TBD":
                picks.append(pick_data)

        # The ESPN site API returns athlete.position as {"id": "8"} with no
        # abbreviation, and college is in athlete.team (already grabbed above).
        # Supplement any blank position fields from the prospects cache (core API
        # athlete details), which does include position abbreviations.
        if any(not p.get("position") for p in picks):
            prospects = self._fetch_all_prospects()
            pos_by_id = {str(p.get("id", "")): p.get("position", "") for p in prospects}
            for pick in picks:
                if not pick.get("position"):
                    pick["position"] = pos_by_id.get(pick.get("_athlete_id", ""), "")

        # Remove the internal tracking key before returning
        for pick in picks:
            pick.pop("_athlete_id", None)

        return picks

    def _get_draft_end_date(self) -> datetime:
        """Return the last Saturday of April for draft_year — the day the draft ends.

        The NFL Draft always concludes on a Saturday in late April. ESPN does not
        expose the exact event end date, so we compute it: find the last Saturday
        of April by working backwards from April 30.
        """
        last_april = datetime(self.draft_year, 4, 30)
        # weekday(): 0=Mon … 5=Sat 6=Sun
        days_back = (last_april.weekday() - 5) % 7
        end_day = last_april - timedelta(days=days_back)
        return end_day.replace(hour=23, minute=59, second=59, microsecond=999999)

    def _is_draft_date(self) -> bool:
        """Check if current date is during NFL Draft week (late April)."""
        now = datetime.now()
        draft_start = datetime(self.draft_year, 4, 20)
        return draft_start <= now <= self._get_draft_end_date()

    def _is_post_draft_window(self, status: Optional[str] = None) -> bool:
        """True if the draft just completed and we are within the post_draft_days window.

        Pass the already-locked status snapshot when calling from display() or
        get_vegas_content() so we never re-read self.draft_status across a
        thread boundary.
        """
        check_status = status if status is not None else self.draft_status
        if check_status != "complete":
            return False
        # Use the current calendar year, not self.draft_year — the ESPN site API
        # has no year parameter and always returns the most recent draft as
        # "complete", but draft_year may have already rolled to next season by May,
        # which would make _get_draft_end_date() return next April and push the
        # window_end a full year into the future.
        now = datetime.now()
        last_april = datetime(now.year, 4, 30)
        days_back = (last_april.weekday() - 5) % 7
        draft_end = (last_april - timedelta(days=days_back)).replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
        window_end = draft_end + timedelta(days=self.post_draft_days)
        return now <= window_end

    def _is_off_season(self) -> bool:
        """True during the NFL off-season (May through January).

        The Super Bowl always falls in February, so months 5-12 and 1 are
        treated as off-season silence. Pre-draft Tankathon mode resumes in
        February once the Super Bowl has cleared.
        """
        month = datetime.now().month
        return month >= 5 or month == 1

    def _get_display_round(self) -> Tuple[int, List[Dict[str, Any]]]:
        """
        Determine which round to show during a live draft.

        Returns the current round if it has at least one completed selection;
        otherwise falls back to the highest round that has completed picks.
        This handles the gap between rounds where current_round has advanced
        but no selections have been announced yet.

        Returns:
            (round_number, picks_list)
        """
        current_picks = [p for p in self.draft_picks if p.get("round") == self.current_round]
        current_done = [p for p in current_picks if p.get("player_name", "TBD") != "TBD"]

        if current_done:
            return self.current_round, current_picks

        # No selections yet in current_round — show last completed round
        completed_rounds = sorted(
            {p.get("round", 0) for p in self.draft_picks
             if p.get("player_name", "TBD") != "TBD"},
            reverse=True
        )
        if completed_rounds:
            last = completed_rounds[0]
            return last, [p for p in self.draft_picks if p.get("round") == last]

        return self.current_round, current_picks  # fallback

    def _get_favorite_team_picks(self, limit: Optional[int] = 3, ascending: bool = False) -> List[Dict[str, Any]]:
        """
        Return picks from configured favorite teams with real player names.

        Args:
            limit: Max picks to return; None returns all.
            ascending: Sort by pick number ascending (post-draft recap order).
        """
        if not self.favorite_teams:
            return []
        fav = [
            p for p in self.draft_picks
            if p.get("team_abbr", "").upper() in self.favorite_teams
            and p.get("player_name", "TBD") != "TBD"
        ]
        fav.sort(key=lambda x: x.get("pick_number", 0), reverse=not ascending)
        return fav if limit is None else fav[:limit]

    def _create_round_label_item(self, round_num: int) -> Image.Image:
        """Create a scroll item showing 'ROUND X' as a section header in gold."""
        text = f"ROUND {round_num}"
        temp_img = Image.new('RGB', (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)
        try:
            w = int(temp_draw.textlength(text, font=self.player_name_font))
        except Exception:
            bbox = temp_draw.textbbox((0, 0), text, font=self.player_name_font)
            w = bbox[2] - bbox[0]
        img = Image.new('RGB', (max(w, 1), self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        y = (self.display_height - self.player_name_font_size) // 2
        draw.text((0, y), text, font=self.player_name_font, fill=(255, 200, 0))
        return img

    def _build_content_items(self, picks: Optional[List[Dict[str, Any]]] = None) -> List[Image.Image]:
        """
        Build the ordered list of scroll images for the current draft state.

        Args:
            picks: Pick snapshot to use for round filtering. Defaults to
                   self.draft_picks when not supplied (e.g. called from
                   _create_draft_scroll_image after the state lock is released).

        Returns:
            List of PIL Images ready for the scroll stream. Empty when there is
            nothing to display for the current state.
        """
        if picks is None:
            picks = self.draft_picks

        content: List[Image.Image] = []

        if self.is_draft_live:
            display_round, round_picks = self._get_display_round()
            content.append(self._create_round_label_item(display_round))
            for pick in self._get_favorite_team_picks():
                img = self._create_pick_item(pick)
                if img:
                    content.append(img)
            for pick in round_picks:
                img = self._create_pick_item(pick)
                if img:
                    content.append(img)

        elif self.draft_status == "complete" or self.simulate_live:
            show = self.post_draft_show
            if show in ("favorites", "both"):
                for pick in self._get_favorite_team_picks(limit=None, ascending=True):
                    img = self._create_pick_item(pick)
                    if img:
                        content.append(img)
            if show in ("rounds", "both"):
                for rnd in range(1, self.display_rounds + 1):
                    round_picks = [
                        p for p in picks
                        if p.get("round") == rnd and p.get("player_name", "TBD") != "TBD"
                    ]
                    if round_picks:
                        content.append(self._create_round_label_item(rnd))
                        for pick in round_picks:
                            img = self._create_pick_item(pick)
                            if img:
                                content.append(img)

        else:
            # Pre-draft
            _, round_picks = self._get_display_round()
            for pick in self._get_favorite_team_picks():
                img = self._create_pick_item(pick)
                if img:
                    content.append(img)
            for pick in round_picks:
                img = self._create_pick_item(pick)
                if img:
                    content.append(img)

        if not content:
            return []

        # Prepend logo only when there are actual pick items to follow it
        items: List[Image.Image] = []
        if self.nfl_draft_logo:
            items.append(self.nfl_draft_logo)
        items.extend(content)
        return items

    def _create_draft_scroll_image(self) -> None:
        """Create scrolling image with all draft picks."""
        # Snapshot status once so both guards see the same value
        status = self.draft_status
        # Silent modes: clear the scroll cache so Vegas mode's scroll_helper
        # fallback doesn't resurrect the old draft-picks image.
        if status == "complete" and not self._is_post_draft_window(status):
            self.scroll_helper.clear_cache()
            return
        if status not in ("live", "complete", "simulate") and self._is_off_season():
            self.scroll_helper.clear_cache()
            return

        content_items = self._build_content_items()

        if content_items:
            self.scroll_helper.create_scrolling_image(
                content_items,
                item_gap=self.item_gap,
                element_gap=8
            )
            self.logger.info(f"Created scroll image with {len(content_items)} items")
        else:
            self.logger.warning("No draft picks to display")

    def _create_pick_item(self, pick: Dict[str, Any]) -> Optional[Image.Image]:
        """
        Create a single pick item image with logo, name, position, and pick number.

        Layout (two lines):
            [LOGO] Player Name
                   POS  #PICK  (School)

        Args:
            pick: Pick data dictionary

        Returns:
            PIL Image for the pick item
        """
        item_height = self.display_height

        # Load team logo
        team_abbr = pick.get("team_abbr", "").upper()
        logo = self._load_team_logo(team_abbr)
        logo_width = logo.width if logo else 0

        # Player name (large font) - top line
        on_clock = pick.get("on_clock", False)
        if on_clock:
            player_name = "On the Clock"
            name_color = (0, 200, 0)
        else:
            player_name = pick.get("player_name", "TBD")
            name_color = self.player_color

        # Build detail line: #PICK  POS  (College)
        detail_parts = []

        # Overall pick number
        detail_parts.append(f"#{pick.get('pick_number', 0)}")

        # Position
        if self.show_position and pick.get("position"):
            detail_parts.append(pick["position"])

        # College (optional)
        if self.show_college and pick.get("college"):
            detail_parts.append(f"({pick['college']})")

        detail_text = "  ".join(detail_parts)

        # Calculate text widths using temp draw context
        temp_img = Image.new('RGB', (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)

        try:
            player_name_width = int(temp_draw.textlength(player_name, font=self.player_name_font))
            detail_width = int(temp_draw.textlength(detail_text, font=self.detail_font))
        except Exception:
            # Fallback for older PIL versions
            player_bbox = temp_draw.textbbox((0, 0), player_name, font=self.player_name_font)
            player_name_width = player_bbox[2] - player_bbox[0]
            detail_bbox = temp_draw.textbbox((0, 0), detail_text, font=self.detail_font)
            detail_width = detail_bbox[2] - detail_bbox[0]

        # Calculate total item width (max of player name or detail line, plus logo)
        element_spacing = 6
        text_width = max(player_name_width, detail_width)
        total_width = logo_width + element_spacing + text_width

        # Create item image
        item_img = Image.new('RGB', (total_width, item_height), (0, 0, 0))
        draw = ImageDraw.Draw(item_img)

        current_x = 0

        # Paste logo (left side, vertically centered)
        if logo:
            logo_y = (item_height - logo.height) // 2
            if logo.mode == 'RGBA':
                item_img.paste(logo, (current_x, logo_y), logo)
            else:
                item_img.paste(logo, (current_x, logo_y))
            current_x += logo_width + element_spacing

        text_start_x = current_x

        # Calculate vertical positions for two-line layout
        # Total text height = player name + small gap + detail line
        line_gap = 2
        total_text_height = self.player_name_font_size + line_gap + self.detail_font_size

        # Center the two lines vertically
        top_y = (item_height - total_text_height) // 2
        player_name_y = top_y
        detail_y = top_y + self.player_name_font_size + line_gap

        # Draw player name (large font, top line)
        draw.text((text_start_x, player_name_y), player_name, font=self.player_name_font, fill=name_color)

        # Draw detail line (small font, bottom line)
        draw.text((text_start_x, detail_y), detail_text, font=self.detail_font, fill=self.pick_color)

        return item_img

    def _load_team_logo(self, team_abbr: str) -> Optional[Image.Image]:
        """Load and resize team logo."""
        if not team_abbr:
            return None

        logo_path = self.logo_base_path / f"{team_abbr}.png"

        logo = self.logo_helper.load_logo(
            team_abbr,
            logo_path,
            max_width=self.logo_size,
            max_height=self.logo_size
        )

        return logo

    def _ensure_logo_installed(self) -> None:
        """
        Copy the bundled nfl_draft_logo.png to the core assets directory if it is not
        already present.  This runs on every startup so the logo is available
        after a fresh plugin install or update.
        """
        target = Path("assets/sports/nfl_logos/nfl_draft_logo.png")
        if target.exists():
            return  # Already installed, nothing to do

        # The logo ships alongside this manager.py file
        source = Path(__file__).parent / "nfl_draft_logo.png"
        if not source.exists():
            self.logger.warning(
                f"Bundled NFL Draft logo not found at {source}; "
                "logo will be unavailable until placed manually"
            )
            return

        try:
            import shutil
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(target))
            self.logger.info(f"Installed NFL Draft logo to {target}")
        except Exception as e:
            self.logger.error(f"Failed to install NFL Draft logo: {e}")

    def _load_nfl_draft_logo(self) -> Optional[Image.Image]:
        """
        Load the NFL Draft logo and return it as a display_height-tall canvas,
        ready to be prepended to the scroll content as the first item.

        Transparent borders are auto-cropped before resizing so that the visible
        logo fills as much vertical space as possible on the display.
        """
        logo_path = Path("assets/sports/nfl_logos/nfl_draft_logo.png")
        if not logo_path.exists():
            self.logger.warning(f"NFL Draft logo not found at {logo_path}")
            return None

        try:
            raw = Image.open(logo_path)
            if raw.mode != 'RGBA':
                raw = raw.convert('RGBA')

            # Crop away transparent borders so only the logo content remains.
            # Without this, a large transparent canvas (e.g. 1000×400 for a
            # logo that only fills ~380×390) would cause the resize to produce
            # a tiny result.
            bbox = raw.getbbox()
            if bbox:
                raw = raw.crop(bbox)

            # Resize to fit within display bounds while preserving aspect ratio
            raw.thumbnail(
                (self.display_width // 2, self.display_height),
                Image.Resampling.LANCZOS
            )

            # Wrap in a full display_height canvas so it composites cleanly
            canvas = Image.new('RGB', (raw.width, self.display_height), (0, 0, 0))
            y = (self.display_height - raw.height) // 2
            canvas.paste(raw, (0, y), raw)

            self.logger.debug(f"Loaded NFL Draft logo ({raw.width}x{raw.height})")
            return canvas

        except Exception as e:
            self.logger.error(f"Error loading NFL Draft logo: {e}")
            return None

    # -------------------------------------------------------------------------
    # Leaders / injuries season gating
    # -------------------------------------------------------------------------

    def _get_superbowl_end_date(self) -> datetime:
        """Return the end of Super Bowl week for the most recently completed season.

        Queries ESPN postseason types/3/weeks/5 which is always labelled
        'Super Bowl' and carries startDate/endDate.  Falls back to Feb 15
        of the current year if the fetch fails.
        """
        year = datetime.now().year
        for yr in (year, year - 1):
            url = (
                f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
                f"/seasons/{yr}/types/3/weeks/5"
            )
            data = self.api_helper.get(url, cache_key=f"nfl_sb_end_{yr}", cache_ttl=86400)
            end_str = (data or {}).get("endDate", "")
            if end_str:
                try:
                    return datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                except ValueError:
                    pass
        return datetime(year, 2, 15, tzinfo=timezone.utc)

    def _is_leaders_active(self) -> bool:
        """True when the injuries mode should produce content.

        Active window: May through 2 weeks after the Super Bowl ends.
        Dark window: post-cutoff February through April (draft season).
        """
        now = datetime.now(timezone.utc)
        # May through January: always active
        if now.month >= 5 or now.month == 1:
            return True
        # Feb and early March: active until 2 weeks post-Super Bowl
        cutoff = self._get_superbowl_end_date() + timedelta(weeks=2)
        return now <= cutoff

    def _is_leaders_season_active(self) -> bool:
        """True only during the NFL regular/post season (Sep–Jan + 2 wks post-SB).

        Leaders pull week-stat data which is meaningless in the offseason.
        """
        now = datetime.now(timezone.utc)
        if now.month >= 9 or now.month == 1:
            return True
        if now.month == 2:
            cutoff = self._get_superbowl_end_date() + timedelta(weeks=2)
            return now <= cutoff
        return False

    # -------------------------------------------------------------------------
    # Data fetchers — leaders / injuries
    # -------------------------------------------------------------------------

    def _get_leaders_url_params(self):
        """Return (season_year, week_or_None) for the most relevant scoreboard."""
        now = datetime.now()
        month, year = now.month, now.year
        if month >= 9:          # Sep-Dec: current regular season, auto week
            return year, None
        elif month == 1:        # January: current season = prior calendar year
            return year - 1, None
        else:                   # Feb-Aug: prior season week 18 as season recap
            return year - 1, 18

    def _fetch_weekly_leaders(self) -> List[Dict[str, Any]]:
        """Fetch NFL game stat leaders from ESPN scoreboard."""
        season_year, week = self._get_leaders_url_params()
        week_tag = str(week) if week else "current"
        cache_key = f"nfl_leaders_{season_year}_{week_tag}"

        # During the active season refresh more aggressively
        now = datetime.now()
        cache_ttl = self.leaders_refresh_interval if now.month not in range(9, 13) else 300

        url = (
            f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
            f"?seasontype=2&dates={season_year}"
        )
        if week:
            url += f"&week={week}"

        data = self.api_helper.get(url, cache_key=cache_key, cache_ttl=cache_ttl)
        if not data:
            return []

        leaders: List[Dict[str, Any]] = []
        week_label = ""

        for event in data.get("events", []):
            if not week_label:
                wk = event.get("week", {})
                if isinstance(wk, dict):
                    num = wk.get("number", "")
                    if num:
                        week_label = f"WK{num}"

            for competition in event.get("competitions", []):
                for group in competition.get("leaders", []):
                    stat_name = group.get("name", "")
                    if stat_name not in self.leaders_stat_types:
                        continue

                    for entry in group.get("leaders", []):
                        athlete = entry.get("athlete", {})
                        team = entry.get("team", {})

                        # Prefer shortName ("P.Mahomes"), build it if absent
                        name = athlete.get("shortName", "")
                        if not name:
                            full = athlete.get("displayName", "")
                            parts = full.split(" ", 1)
                            name = f"{parts[0][0]}.{parts[1]}" if len(parts) == 2 else full

                        pos_obj = athlete.get("position", {})
                        position = pos_obj.get("abbreviation", "") if isinstance(pos_obj, dict) else ""

                        leaders.append({
                            "name": name,
                            "position": position,
                            "team_abbr": team.get("abbreviation", ""),
                            "stat_line": entry.get("displayValue", ""),
                            "stat_value": float(entry.get("value", 0)),
                            "stat_type": stat_name,
                            "week_label": week_label,
                            "season_year": season_year,
                        })

        return leaders

    def _fetch_injury_report(self) -> List[Dict[str, Any]]:
        """Fetch NFL injury / OTA status report from ESPN."""
        cache_key = "nfl_injuries"
        cache_ttl = self.injuries_refresh_interval

        url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
        data = self.api_helper.get(url, cache_key=cache_key, cache_ttl=cache_ttl)
        if not data:
            return []

        players: List[Dict[str, Any]] = []

        for team_entry in data.get("injuries", []):
            for injury in team_entry.get("injuries", []):
                athlete = injury.get("athlete", {})
                team_abbr = athlete.get("team", {}).get("abbreviation", "")
                status = injury.get("status", "")

                pos_obj = athlete.get("position", {})
                position = pos_obj.get("abbreviation", "") if isinstance(pos_obj, dict) else ""

                # Position filter
                if self.injury_positions and position not in self.injury_positions:
                    continue

                # Status filter: always include configured statuses;
                # optionally include Active players that have a note (OTA news)
                include = status in self.injury_statuses
                if not include and status == "Active" and self.show_ota_active:
                    comment = injury.get("shortComment", "")
                    include = bool(comment)

                if not include:
                    continue

                # Prefer shortName, fall back to displayName
                name = athlete.get("shortName", "") or athlete.get("displayName", "")

                comment = injury.get("shortComment", "")
                # Strip trailing reporter attribution: ", Name of Outlet reports."
                comment = re.sub(r',\s+\S.*?\breports?\.\s*$', '', comment, flags=re.IGNORECASE).strip()

                players.append({
                    "name": name,
                    "position": position,
                    "team_abbr": team_abbr,
                    "status": status,
                    "comment": comment,
                })

        return players

    # -------------------------------------------------------------------------
    # Render helpers — leaders / injuries
    # -------------------------------------------------------------------------

    # Status → display label + color
    _STATUS_STYLE: Dict[str, tuple] = {
        "Out":            ("OUT",   (255,  50,  50)),
        "Doubtful":       ("DBT",   (255, 130,   0)),
        "Questionable":   ("Q",     (255, 200,   0)),
        "Injured Reserve":("IR",    (180,  80, 255)),
        "Active":         ("OTA",   (100, 220, 100)),
    }

    _STAT_LABEL: Dict[str, str] = {
        "passingYards":   "PASSING",
        "rushingYards":   "RUSHING",
        "receivingYards": "RECEIVING",
    }

    def _create_section_header(self, text: str, color: tuple = (255, 200, 0)) -> Image.Image:
        """Create a scroll header card (same pattern as _create_round_label_item)."""
        temp = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(temp)
        try:
            w = int(draw.textlength(text, font=self.player_name_font))
        except Exception:
            bbox = draw.textbbox((0, 0), text, font=self.player_name_font)
            w = bbox[2] - bbox[0]
        img = Image.new("RGB", (max(w, 1), self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        y = (self.display_height - self.player_name_font_size) // 2
        draw.text((0, y), text, font=self.player_name_font, fill=color)
        return img

    def _create_leader_item(self, player: Dict[str, Any]) -> Optional[Image.Image]:
        """Render a single stat-leader card: [LOGO] Name  POS / stat line."""
        team_abbr = player.get("team_abbr", "").upper()
        logo = self._load_team_logo(team_abbr)
        logo_width = logo.width if logo else 0

        name_line = player.get("name", "")
        pos = player.get("position", "")
        if pos:
            name_line = f"{name_line}  {pos}"
        stat_line = player.get("stat_line", "")

        temp = Image.new("RGB", (1, 1))
        td = ImageDraw.Draw(temp)
        try:
            name_w = int(td.textlength(name_line, font=self.player_name_font))
            stat_w = int(td.textlength(stat_line, font=self.detail_font))
        except Exception:
            nb = td.textbbox((0, 0), name_line, font=self.player_name_font)
            name_w = nb[2] - nb[0]
            sb = td.textbbox((0, 0), stat_line, font=self.detail_font)
            stat_w = sb[2] - sb[0]

        gap = 6
        text_w = max(name_w, stat_w)
        total_w = logo_width + gap + text_w
        img = Image.new("RGB", (max(total_w, 1), self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        x = 0
        if logo:
            ly = (self.display_height - logo.height) // 2
            if logo.mode == "RGBA":
                img.paste(logo, (x, ly), logo)
            else:
                img.paste(logo, (x, ly))
            x += logo_width + gap

        line_gap = 2
        total_text_h = self.player_name_font_size + line_gap + self.detail_font_size
        top_y = (self.display_height - total_text_h) // 2
        draw.text((x, top_y), name_line, font=self.player_name_font, fill=self.player_color)
        draw.text((x, top_y + self.player_name_font_size + line_gap),
                  stat_line, font=self.detail_font, fill=(255, 255, 255))
        return img

    def _create_injury_item(self, player: Dict[str, Any], show_logo: bool = True) -> Optional[Image.Image]:
        """Render a single injury card: [LOGO] Name  POS / STATUS · comment."""
        team_abbr = player.get("team_abbr", "").upper()
        logo = self._load_team_logo(team_abbr) if show_logo else None
        logo_width = logo.width if logo else 0

        name_line = player.get("name", "")
        pos = player.get("position", "")
        if pos:
            name_line = f"{name_line}  {pos}"

        status_raw = player.get("status", "")
        label, status_color = self._STATUS_STYLE.get(status_raw, (status_raw[:3].upper(), (200, 200, 200)))
        comment = player.get("comment", "")
        detail_line = f"{label}" + (f"  {comment}" if comment else "")

        temp = Image.new("RGB", (1, 1))
        td = ImageDraw.Draw(temp)
        try:
            name_w = int(td.textlength(name_line, font=self.player_name_font))
            detail_w = int(td.textlength(detail_line, font=self.injury_detail_font))
        except Exception:
            nb = td.textbbox((0, 0), name_line, font=self.player_name_font)
            name_w = nb[2] - nb[0]
            db = td.textbbox((0, 0), detail_line, font=self.injury_detail_font)
            detail_w = db[2] - db[0]

        gap = 6
        text_w = max(name_w, detail_w)
        total_w = logo_width + gap + text_w
        img = Image.new("RGB", (max(total_w, 1), self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        x = 0
        if logo:
            ly = (self.display_height - logo.height) // 2
            if logo.mode == "RGBA":
                img.paste(logo, (x, ly), logo)
            else:
                img.paste(logo, (x, ly))
            x += logo_width + gap

        line_gap = 2
        total_text_h = self.player_name_font_size + line_gap + self.injury_detail_font_size
        top_y = (self.display_height - total_text_h) // 2
        draw.text((x, top_y), name_line, font=self.player_name_font, fill=self.player_color)

        # Draw status label in status color, then comment in white — both at injury_detail_font
        label_text = label
        rest_text = f"  {comment}" if comment else ""
        try:
            label_px = int(draw.textlength(label_text, font=self.injury_detail_font))
        except Exception:
            lb = draw.textbbox((0, 0), label_text, font=self.injury_detail_font)
            label_px = lb[2] - lb[0]
        detail_y = top_y + self.player_name_font_size + line_gap
        draw.text((x, detail_y), label_text, font=self.injury_detail_font, fill=status_color)
        if rest_text:
            draw.text((x + label_px, detail_y), rest_text, font=self.injury_detail_font, fill=(200, 200, 200))

        return img

    def _build_leaders_content(self) -> List[Image.Image]:
        """Build ordered scroll items for the leaders ticker."""
        players = self.leaders_data
        if not players:
            return []

        # Determine header label
        sample = players[0] if players else {}
        week_label = sample.get("week_label", "")
        season_year = sample.get("season_year", "")
        if week_label:
            header_text = f"NFL {week_label} LEADERS"
        elif season_year:
            header_text = f"NFL {season_year} LEADERS"
        else:
            header_text = "NFL LEADERS"

        items: List[Image.Image] = [self._create_section_header(header_text, (255, 200, 0))]

        # Group by stat type, each group gets a sub-header and sorted players
        for stat_key in self.leaders_stat_types:
            group = [p for p in players if p.get("stat_type") == stat_key]
            if not group:
                continue
            group.sort(key=lambda p: p.get("stat_value", 0), reverse=True)
            group = group[:3]
            sub_label = self._STAT_LABEL.get(stat_key, stat_key.upper())
            items.append(self._create_section_header(sub_label, (100, 180, 255)))
            for player in group:
                img = self._create_leader_item(player)
                if img:
                    items.append(img)

        return items

    def _build_injury_content(self) -> List[Image.Image]:
        """Build ordered scroll items for the injury ticker, grouped by team."""
        players = self.injuries_data
        if not players:
            return []

        items: List[Image.Image] = [
            self._create_section_header("NFL INJURIES", (255, 100, 0))
        ]

        # Group players by team, preserving order of first appearance
        teams_order: List[str] = []
        by_team: Dict[str, List[Dict[str, Any]]] = {}
        for player in players:
            abbr = player.get("team_abbr", "")
            if abbr not in by_team:
                by_team[abbr] = []
                teams_order.append(abbr)
            by_team[abbr].append(player)

        for team_abbr in teams_order:
            logo = self._load_team_logo(team_abbr)
            if logo:
                items.append(logo)
            for player in by_team[team_abbr]:
                img = self._create_injury_item(player, show_logo=False)
                if img:
                    items.append(img)

        return items

    def _create_leaders_scroll_image(self) -> None:
        """Build and cache the leaders scrolling image."""
        if not self._is_leaders_season_active():
            self.scroll_helper.clear_cache()
            return
        content = self._build_leaders_content()
        if content:
            self.scroll_helper.create_scrolling_image(content, item_gap=self.item_gap, element_gap=8)
        else:
            self.scroll_helper.clear_cache()

    def _create_injuries_scroll_image(self) -> None:
        """Build and cache the injuries scrolling image."""
        if not self._is_leaders_active():
            self.scroll_helper.clear_cache()
            return
        content = self._build_injury_content()
        if content:
            self.scroll_helper.create_scrolling_image(content, item_gap=self.item_gap, element_gap=8)
        else:
            self.scroll_helper.clear_cache()

    # -------------------------------------------------------------------------
    # Update helpers — leaders / injuries
    # -------------------------------------------------------------------------

    def _update_leaders(self) -> None:
        """Fetch leaders data and rebuild scroll image."""
        current_time = time.time()
        if (self.last_leaders_update is not None
                and current_time - self.last_leaders_update < self.leaders_refresh_interval):
            return

        if not self._is_leaders_season_active():
            self.scroll_helper.clear_cache()
            return

        self.logger.info("Updating NFL leaders data")
        try:
            self.leaders_data = self._fetch_weekly_leaders()
            self._create_leaders_scroll_image()
            self.last_leaders_update = current_time
            self.logger.info(f"Loaded {len(self.leaders_data)} leader entries")
        except Exception as e:
            self.logger.error(f"Error updating leaders: {e}", exc_info=True)

    def _update_injuries(self) -> None:
        """Fetch injury data and rebuild scroll image."""
        current_time = time.time()
        if (self.last_injuries_update is not None
                and current_time - self.last_injuries_update < self.injuries_refresh_interval):
            return

        if not self._is_leaders_active():
            self.scroll_helper.clear_cache()
            return

        self.logger.info("Updating NFL injury data")
        try:
            self.injuries_data = self._fetch_injury_report()
            self._create_injuries_scroll_image()
            self.last_injuries_update = current_time
            self.logger.info(f"Loaded {len(self.injuries_data)} injury entries")
        except Exception as e:
            self.logger.error(f"Error updating injuries: {e}", exc_info=True)

    def update(self) -> None:
        """
        Fetch/update draft data from ESPN API.

        Called based on update_interval in manifest.
        Implements dual-mode logic:
        - During live draft: refresh every 10 minutes, show current round only
        - Off-season: daily refresh, show projected picks for configured rounds
        """
        current_time = time.time()

        # Use live_refresh_interval whenever the draft is active or we are
        # inside the date window (April 20-27) so polling ramps up automatically
        # on draft day even before ESPN flips state to "in".  Off-season this
        # returns quickly — the framework calls update() every 5 minutes but the
        # 24-hour projection_refresh_interval keeps us from hitting the API.
        in_draft_window = self._is_draft_date()
        refresh_interval = (
            self.live_refresh_interval
            if (self.is_draft_live or in_draft_window)
            else self.projection_refresh_interval
        )

        # Check if refresh is needed
        if self.last_update_time is not None and current_time - self.last_update_time < refresh_interval:
            return

        # Refresh leaders only during the season; injuries year-round when active
        if self._is_leaders_season_active():
            self._update_leaders()
        if self._is_leaders_active():
            self._update_injuries()

        self.logger.info(f"Updating NFL Draft data (live={self.is_draft_live}, year={self.draft_year}, simulate={self.simulate_live})")

        try:
            if self.simulate_live:
                new_picks = self._fetch_historical_picks()
                new_status = "simulate"
                new_live = False
                new_round = 1
            else:
                new_picks = self._fetch_draft_picks()
                if self.draft_status == "pre":
                    new_picks = [p for p in new_picks if p.get("round") == 1]
                new_status = self.draft_status
                new_live = self.is_draft_live
                new_round = self.current_round

            new_picks.sort(key=lambda x: x.get("pick_number", 0))

            for pick in new_picks:
                pick.pop("on_clock", None)
            if new_live and not self.simulate_live:
                for pick in new_picks:
                    if pick.get("player_name") == "TBD" and pick.get("round") == new_round:
                        pick["on_clock"] = True
                        break

            with self._state_lock:
                self.draft_status = new_status
                self.is_draft_live = new_live
                self.current_round = new_round
                self.draft_picks = new_picks

            # Build scroll image after the lock so _create_draft_scroll_image
            # reads a fully consistent state snapshot.
            self._create_draft_scroll_image()

            self.last_update_time = current_time
            self.logger.info(f"Loaded {len(new_picks)} draft picks")

        except Exception as e:
            self.logger.error(f"Error updating draft data: {e}", exc_info=True)

    def display(self, force_clear: bool = False, display_mode: str = None) -> None:
        """
        Render the draft picks to the LED matrix.

        Uses ScrollHelper to create smooth horizontal scrolling.

        Args:
            force_clear: If True, clear display before rendering
            display_mode: Active display mode name passed by the display controller
        """
        if force_clear:
            self.display_manager.clear()

        # Leaders / injuries modes share the same scroll render path
        _active_mode = display_mode or ""
        if _active_mode == "nfl_leaders_ticker":
            if not self._is_leaders_season_active():
                self._display_blank()
                return
        elif _active_mode == "nfl_injuries_ticker":
            if not self._is_leaders_active():
                self._display_blank()
                return
        if _active_mode in ("nfl_leaders_ticker", "nfl_injuries_ticker"):
            try:
                self.scroll_helper.update_scroll_position()
                visible = self.scroll_helper.get_visible_portion()
                if visible:
                    self.display_manager.image = visible
                    self.display_manager.update_display()
            except Exception as e:
                self.logger.error(f"Error displaying {self.plugin_id}: {e}")
                self._display_error()
            return

        with self._state_lock:
            picks_loaded = bool(self.draft_picks)
            status = self.draft_status

        # Off-season / expired post-draft window: render nothing
        if status == "complete" and not self._is_post_draft_window(status):
            self._display_blank()
            return
        if status not in ("live", "complete", "simulate") and self._is_off_season():
            self._display_blank()
            return

        if not picks_loaded:
            self._display_no_data()
            return

        try:
            # Update scroll position
            self.scroll_helper.update_scroll_position()

            # Get visible portion
            visible_image = self.scroll_helper.get_visible_portion()

            if visible_image:
                # Set image to display manager
                self.display_manager.image = visible_image
                self.display_manager.update_display()

        except Exception as e:
            self.logger.error(f"Error displaying draft: {e}")
            self._display_error()

    def _display_blank(self) -> None:
        """Render a solid black frame (off-season silence — no text, no errors)."""
        img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
        self.display_manager.image = img
        self.display_manager.update_display()

    def _display_no_data(self) -> None:
        """Display a no data message."""
        img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        message = "No Draft Data"
        try:
            text_width = draw.textlength(message, font=self.detail_font)
        except Exception:
            bbox = draw.textbbox((0, 0), message, font=self.detail_font)
            text_width = bbox[2] - bbox[0]

        x = (self.display_width - text_width) // 2
        y = (self.display_height - self.detail_font_size) // 2

        draw.text((x, y), message, font=self.detail_font, fill=(150, 150, 150))

        self.display_manager.image = img
        self.display_manager.update_display()

    def _display_error(self) -> None:
        """Display an error message."""
        img = Image.new('RGB', (self.display_width, self.display_height), (50, 0, 0))
        draw = ImageDraw.Draw(img)

        message = "Error"
        try:
            text_width = draw.textlength(message, font=self.detail_font)
        except Exception:
            bbox = draw.textbbox((0, 0), message, font=self.detail_font)
            text_width = bbox[2] - bbox[0]

        x = (self.display_width - text_width) // 2
        y = (self.display_height - self.detail_font_size) // 2

        draw.text((x, y), message, font=self.detail_font, fill=(255, 100, 100))

        self.display_manager.image = img
        self.display_manager.update_display()

    def supports_dynamic_duration(self) -> bool:
        """Enable dynamic duration based on scroll completion."""
        return self.dynamic_duration_enabled

    def is_cycle_complete(self) -> bool:
        """Check if scroll cycle is complete."""
        return self.scroll_helper.is_scroll_complete()

    def reset_cycle_state(self) -> None:
        """Reset scroll state for new cycle."""
        self.scroll_helper.reset_scroll()

    def get_display_duration(self) -> float:
        """Get display duration, using dynamic duration from scroll helper."""
        if self.supports_dynamic_duration():
            return float(self.scroll_helper.get_dynamic_duration())
        return self.config.get('display_duration', 60.0)

    # -------------------------------------------------------------------------
    # Vegas scroll mode support
    # -------------------------------------------------------------------------

    def get_vegas_content_type(self) -> str:
        """Report as multi-item content so Vegas uses SCROLL mode by default."""
        return 'multi'

    def get_vegas_content(self) -> Optional[List[Image.Image]]:
        """
        Return one image per draft pick for Vegas scroll mode.

        Vegas composes these individually into the continuous scroll stream,
        giving smoother integration than handing it the pre-built scroll image.
        Returns None if no picks are loaded yet.
        """
        with self._state_lock:
            picks = list(self.draft_picks)
            status = self.draft_status

        # Try draft content first
        if picks:
            if not (status == "complete" and not self._is_post_draft_window(status)):
                if not (status not in ("live", "complete", "simulate") and self._is_off_season()):
                    images = self._build_content_items(picks=picks)
                    if images:
                        return images

        # No draft content — fall back to off-season leaders + injuries
        content = []
        if self._is_leaders_season_active() and self.leaders_data:
            imgs = self._build_leaders_content()
            if imgs:
                content.extend(imgs)
        if self._is_leaders_active() and self.injuries_data:
            imgs = self._build_injury_content()
            if imgs:
                content.extend(imgs)
        if content:
            return content

        return None

    def has_live_priority(self) -> bool:
        """Check if live priority is enabled."""
        return self.config.get("live_priority", False)

    def has_live_content(self) -> bool:
        """Check if draft is currently live."""
        return self.is_draft_live and self.draft_status == "live"

    def get_live_modes(self) -> List[str]:
        """Return display modes for live content."""
        return ["nfl_draft_ticker"]

    def validate_config(self) -> bool:
        """Validate plugin configuration."""
        return super().validate_config()

    def get_info(self) -> Dict[str, Any]:
        """Return plugin info for web UI."""
        info = super().get_info()
        info.update({
            'draft_year': self.draft_year,
            'is_live': self.is_draft_live,
            'draft_status': self.draft_status,
            'current_round': self.current_round,
            'picks_loaded': len(self.draft_picks),
        })
        return info

    def cleanup(self) -> None:
        """Cleanup resources."""
        if hasattr(self, 'scroll_helper'):
            self.scroll_helper.clear_cache()
        if hasattr(self, 'logo_helper'):
            self.logo_helper.clear_cache()
        super().cleanup()

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        """Handle configuration changes."""
        super().on_config_change(new_config)
        self._load_config()
        self.player_name_font = self._load_font(self.player_name_font_size)
        self.detail_font = self._load_font(self.detail_font_size)

        # Force data refresh on config change
        self.last_update_time = None

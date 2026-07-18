"""
Win Celebration Plugin for LEDMatrix

Displays animated GIF celebrations on the LED matrix when configured teams win.
Supports multiple teams simultaneously — each team can have its own celebration
GIF, win text, and brand colors. When multiple teams win on the same day each
celebration fires independently and rotates on the display.

Supported sports via the ESPN public scoreboard API (no key required):
  mlb, nfl, nba, nhl

API Version: 1.0.0
"""

import math
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageSequence

from src.plugin_system.base_plugin import BasePlugin, VegasDisplayMode
from src.common import APIHelper

# ESPN public scoreboard endpoints — no API key required
ESPN_ENDPOINTS: Dict[str, str] = {
    "mlb": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
    "nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "nhl": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
}

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
DIM_RED = (200, 80, 80)   # opponent score colour


def _to_rgb(value: Any, default: Tuple[int, int, int]) -> Tuple[int, int, int]:
    """Convert a config [R, G, B] list to an int tuple, falling back to default."""
    try:
        r, g, b = value
        return (int(r), int(g), int(b))
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Per-team mutable state
# ---------------------------------------------------------------------------

class _TeamState:
    """All mutable state for one configured team."""

    def __init__(self, team_cfg: Dict[str, Any]) -> None:
        # Config snapshot (immutable after init)
        self.abbreviation: str = team_cfg.get("abbreviation", "").upper()
        self.sport: str = team_cfg.get("sport", "mlb").lower()
        gif_upload = team_cfg.get("gif_upload")
        if gif_upload and isinstance(gif_upload, list) and gif_upload:
            _up_path = gif_upload[0].get("path", "")
            self.gif_path: Path = Path(_up_path) if _up_path else Path(__file__).parent / team_cfg.get("gif_file", "celebrate.gif")
        else:
            self.gif_path = Path(__file__).parent / team_cfg.get("gif_file", "celebrate.gif")
        self.win_text: str = team_cfg.get("win_text", f"{self.abbreviation} WINS!")
        self.animation_style: str = team_cfg.get("animation_style", "waving_flag")
        self.primary_color: Tuple[int, int, int] = _to_rgb(
            team_cfg.get("primary_color", [255, 255, 255]), WHITE
        )
        self.secondary_color: Tuple[int, int, int] = _to_rgb(
            team_cfg.get("secondary_color", [0, 0, 0]), BLACK
        )

        # Win state
        self.celebrating: bool = False
        self.win_expires_at: Optional[datetime] = None
        self.last_win_score: str = ""
        self.win_info: Dict[str, Any] = {}

        # Schedule-aware skip: avoid calling ESPN on no-game days
        self.game_today: Optional[bool] = None  # None = not yet checked
        self.game_date: Optional[date] = None

        # Animation
        self.frames: List[Image.Image] = []
        self.frame_durations: List[float] = []
        self.frame_index: int = 0
        self.last_frame_time: float = 0.0

        # Flash state for win-text overlay
        self.flash_on: bool = True
        self.flash_last_toggle: float = 0.0

        # Live-priority: fires once per new win to trigger an immediate takeover
        self.live_priority_fired: bool = False

        # Display logging: fires once per win when the celebration first hits the matrix
        self.display_logged: bool = False


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

class WinCelebrationPlugin(BasePlugin):
    """
    Multi-team win celebration plugin.

    Monitors ESPN scoreboards and activates animated GIF celebrations when any
    configured team wins. Multiple teams can celebrate simultaneously — each
    team's animation is shown in rotation, controlled by team_display_duration.

    Place each team's GIF file alongside manager.py (or configure gif_file with
    a path relative to the plugin directory).

    Configuration options (see config_schema.json for full details):
        teams (list)               Required. List of team config objects.
        celebration_hours (float)  Hours to celebrate after a win. Default 1.0.
        animation_fps (float)      FPS for programmatic fallback animation. Default 12.0.
        show_score (bool)          Overlay final score. Default true.
        show_text (bool)           Overlay win text. Default true.
        team_display_duration (int) Seconds per celebrating team per display slot. Default 30.
        update_interval (int)      ESPN API poll interval in seconds. Default 300.
        font_name (str)            Font filename in assets/fonts/. Default "4x6-font.ttf".
        font_size (int)            Font size. Default 6.
        simulate_win (bool)        Simulate a win for testing. Default false.
        simulate_team (int)        Slot number to simulate (1–5). Use 0 for first active slot.
    """

    def __init__(
        self,
        plugin_id: str,
        config: Dict[str, Any],
        display_manager: Any,
        cache_manager: Any,
        plugin_manager: Any,
    ) -> None:
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        self.display_width: int = display_manager.matrix.width
        self.display_height: int = display_manager.matrix.height

        self.api_helper = APIHelper(cache_manager=cache_manager, logger=self.logger)

        self._load_config()
        self._load_fonts()

        # Per-sport last-update timestamps for independent throttling
        self._last_sport_update: Dict[str, Optional[datetime]] = {}

        # Build per-team state objects from config, keyed by slot number (1-5)
        self._team_states: Dict[int, _TeamState] = {}
        for slot, team_cfg in enumerate(self._teams_config, 1):
            state = _TeamState(team_cfg)
            if not state.abbreviation:
                self.logger.warning("Team slot %d missing 'abbreviation', skipping", slot)
                continue
            self._team_states[slot] = state

        # Multi-team display cycling state
        self._current_team_slot: int = 0
        self._current_team_start: float = 0.0

        # Signal high-FPS mode to the display controller for smooth GIF animation
        self.enable_scrolling: bool = True

        # Cache for team logo images (loaded once, reused across frames)
        self._logo_cache: Dict[str, Optional[Image.Image]] = {}

        self._flash_period: float = 0.5   # seconds between win-text flashes

        # Pre-load GIF frames for every configured team
        for state in self._team_states.values():
            self._build_frames(state)

        # Activate simulation immediately so live-priority fires even when the
        # display controller uses fast-startup (cached-data) mode and delays
        # the first update() call.
        if self.simulate_win:
            target = self._resolve_simulate_target()
            if target:
                self._trigger_simulation(target)

        self.logger.info(
            "Win Celebration plugin initialised — %d team(s): %s (display %dx%d)",
            len(self._team_states),
            ", ".join(f"{slot}:{s.abbreviation}" for slot, s in self._team_states.items()),
            self.display_width,
            self.display_height,
        )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        # Build the teams list from named slots team1–team5.
        # Slots with a blank abbreviation are silently skipped.
        self._teams_config: List[Dict[str, Any]] = [
            self.config[slot]
            for slot in ("team1", "team2", "team3", "team4", "team5")
            if self.config.get(slot, {}).get("abbreviation", "").strip()
        ]
        self.update_interval_seconds: int = int(self.config.get("update_interval", 300))
        self.celebration_hours: float = float(self.config.get("celebration_hours", 1.0))
        self.animation_fps: float = float(self.config.get("animation_fps", 12.0))
        self.show_score: bool = bool(self.config.get("show_score", True))
        self.show_text: bool = bool(self.config.get("show_text", True))
        self.team_display_duration: int = int(self.config.get("team_display_duration", 30))
        self.font_name: str = self.config.get("font_name", "4x6-font.ttf")
        self.font_size: int = int(self.config.get("font_size", 6))
        self.simulate_win: bool = bool(self.config.get("simulate_win", False))
        self.simulate_team: int = int(self.config.get("simulate_team", 0) or 0)

    def _load_fonts(self) -> None:
        try:
            font_path = Path("assets/fonts") / self.font_name
            if font_path.exists():
                self.font = ImageFont.truetype(str(font_path), self.font_size)
            else:
                self.font = ImageFont.load_default()
                self.logger.warning("Font %s not found, using default", self.font_name)
        except Exception as exc:
            self.logger.error("Error loading font: %s", exc)
            self.font = ImageFont.load_default()

    # ------------------------------------------------------------------
    # Animation frame generation
    # ------------------------------------------------------------------

    def _build_frames(self, state: _TeamState) -> None:
        """Load GIF frames for a team, falling back to a programmatic animation."""
        if state.gif_path.exists():
            if self._load_gif_frames(state):
                state.frame_index = 0
                state.last_frame_time = 0.0
                return
            self.logger.warning(
                "[%s] GIF load failed, using programmatic fallback", state.abbreviation
            )
        else:
            self.logger.warning(
                "[%s] GIF not found at %s — using programmatic fallback",
                state.abbreviation, state.gif_path,
            )
        self._build_programmatic_frames(state)
        state.frame_index = 0
        state.last_frame_time = 0.0

    def _load_gif_frames(self, state: _TeamState) -> bool:
        """
        Extract frames from the team's GIF, resize to fit the display, and
        composite onto a black canvas.  Returns True on success.
        """
        try:
            gif = Image.open(state.gif_path)
            w, h = self.display_width, self.display_height
            frames: List[Image.Image] = []
            durations: List[float] = []

            for raw_frame in ImageSequence.Iterator(gif):
                duration_ms = raw_frame.info.get("duration", 100)
                # GIFs with duration=0 are treated as 100ms by browsers — apply same standard
                if not duration_ms:
                    duration_ms = 100
                durations.append(duration_ms / 1000.0)

                frame_rgba = raw_frame.convert("RGBA")
                frame_rgba.thumbnail((w, h), Image.Resampling.LANCZOS)

                canvas = Image.new("RGB", (w, h), BLACK)
                paste_x = (w - frame_rgba.width) // 2
                paste_y = (h - frame_rgba.height) // 2
                canvas.paste(frame_rgba, (paste_x, paste_y), frame_rgba)
                frames.append(canvas)

            if not frames:
                return False

            state.frames = frames
            state.frame_durations = durations
            self.logger.info(
                "[%s] Loaded %d frames from %s",
                state.abbreviation, len(frames), state.gif_path.name,
            )
            return True

        except Exception as exc:
            self.logger.error("[%s] Error loading GIF: %s", state.abbreviation, exc, exc_info=True)
            return False

    def _build_programmatic_frames(self, state: _TeamState, num_frames: int = 24) -> None:
        """Generate a programmatic animation using the team's brand colors."""
        frame_duration = 1.0 / max(self.animation_fps, 1.0)
        w, h = self.display_width, self.display_height
        if state.animation_style == "skull_crossbones":
            renderer = self._render_skull_frame
        elif state.animation_style == "team_logo":
            renderer = self._render_logo_frame
        else:
            renderer = self._render_flag_frame
        state.frames = [renderer(state, w, h, i, num_frames) for i in range(num_frames)]
        state.frame_durations = [frame_duration] * num_frames
        self.logger.debug(
            "[%s] Built %d programmatic frames (style=%s)",
            state.abbreviation, num_frames, state.animation_style,
        )

    def _render_flag_frame(
        self, state: _TeamState, w: int, h: int, frame_idx: int, num_frames: int
    ) -> Image.Image:
        """Render a single waving-flag frame using the team's primary/secondary colors."""
        img = Image.new("RGB", (w, h), BLACK)
        phase = (2 * math.pi * frame_idx) / num_frames

        flag_w = int(w * 0.6)
        flag_h = int(h * 0.75)
        flag_top = (h - flag_h) // 2
        amplitude = max(1, flag_h // 8)

        for col in range(flag_w):
            wave_factor = col / max(flag_w - 1, 1)
            offset = int(amplitude * wave_factor * math.sin(phase + col * 0.3))
            col_top = flag_top + offset
            mid = col_top + flag_h // 2
            for row in range(col_top, col_top + flag_h):
                if 0 <= row < h:
                    img.putpixel((col, row), state.primary_color if row < mid else state.secondary_color)

        # Pole
        for row in range(flag_top - 2, flag_top + flag_h + 2):
            if 0 <= row < h:
                img.putpixel((0, row), WHITE)

        # Team abbreviation centred on the flag
        draw = ImageDraw.Draw(img)
        abbr = state.abbreviation
        bbox = draw.textbbox((0, 0), abbr, font=self.font)
        text_w = bbox[2] - bbox[0]
        cx = flag_w // 2
        wave_offset = int(amplitude * 0.5 * math.sin(phase + cx * 0.3))
        cy = flag_top + flag_h // 2 + wave_offset
        self._draw_small_text(draw, abbr, cx - text_w // 2, cy - self.font_size // 2, WHITE)

        # Win text to the right of the flag
        if self.show_text:
            self._draw_small_text(draw, "WIN!", flag_w + 2, 2 + self.font_size + 1, state.primary_color)

        return img

    def _render_skull_frame(
        self, state: _TeamState, w: int, h: int, frame_idx: int, num_frames: int
    ) -> Image.Image:
        """
        Render a single skull-and-crossbones frame.

        The skull head is drawn centered slightly above the vertical midpoint,
        with two crossed bones behind it. All elements scale proportionally to
        the display dimensions so the animation looks good on any panel size.

        Animation cycle (over num_frames):
          - Skull pulses in brightness (sine wave, 60–100 % of primary color)
          - Eye sockets flash bright (pale amber) for 2 frames every quarter-cycle
        """
        img = Image.new("RGB", (w, h), BLACK)
        draw = ImageDraw.Draw(img)

        phase = (2 * math.pi * frame_idx) / num_frames
        # Brightness oscillates between 0.6 and 1.0
        brightness = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(phase))

        def _dim(color: Tuple[int, int, int], b: float) -> Tuple[int, int, int]:
            return (
                max(0, min(255, int(color[0] * b))),
                max(0, min(255, int(color[1] * b))),
                max(0, min(255, int(color[2] * b))),
            )

        skull_color = _dim(state.primary_color, brightness)
        # Crossbones use secondary color if non-black, else dim primary
        bone_base = state.secondary_color if any(c > 30 for c in state.secondary_color) else state.primary_color
        bone_color = _dim(bone_base, brightness * 0.75)

        # ── Skull geometry (all values scale with display size) ──────────────
        skull_rx = max(4, min(w // 8, h // 3))  # capped so crossbones extend visibly beyond skull
        skull_ry = max(3, h // 4)          # vertical radius
        skull_cx = w // 2
        skull_cy = h // 2 - skull_ry // 3  # sit slightly above centre

        # ── 1. Crossbones (behind skull) ──────────────────────────────────
        # Use separate x/y extents so bones spread wide on landscape panels.
        # bone_y is constrained by panel height; bone_x reaches toward the
        # display edges, making the X clearly visible on wide/short panels.
        bone_y = (h - 4) // 2
        bone_x = max(bone_y, skull_cx - skull_rx - 4)
        bone_half = max(1, skull_rx // 4)  # half-thickness of each bone

        # Bone 1: top-left ↘ bottom-right
        for off in range(-bone_half, bone_half + 1):
            draw.line(
                [(skull_cx - bone_x + off, skull_cy - bone_y),
                 (skull_cx + bone_x + off, skull_cy + bone_y)],
                fill=bone_color,
            )
        # Bone 2: top-right ↙ bottom-left
        for off in range(-bone_half, bone_half + 1):
            draw.line(
                [(skull_cx + bone_x + off, skull_cy - bone_y),
                 (skull_cx - bone_x + off, skull_cy + bone_y)],
                fill=bone_color,
            )
        # Round knobs at bone ends
        knob_r = max(1, bone_half + 1)
        for bx, by in [
            (skull_cx - bone_x, skull_cy - bone_y),
            (skull_cx + bone_x, skull_cy + bone_y),
            (skull_cx + bone_x, skull_cy - bone_y),
            (skull_cx - bone_x, skull_cy + bone_y),
        ]:
            draw.ellipse([bx - knob_r, by - knob_r, bx + knob_r, by + knob_r], fill=bone_color)

        # ── 2. Skull cranium (filled ellipse) ────────────────────────────
        draw.ellipse(
            [skull_cx - skull_rx, skull_cy - skull_ry,
             skull_cx + skull_rx, skull_cy + skull_ry],
            fill=skull_color,
        )

        # ── 3. Eye sockets ───────────────────────────────────────────────
        eye_r  = max(1, skull_rx // 3)
        eye_y  = skull_cy - skull_ry // 5
        eye_dx = skull_rx // 2

        # Eye flash: 2 frames per quarter-cycle
        eye_fill = BLACK
        quarter = num_frames // 4
        if quarter > 0 and (frame_idx % quarter) < 2:
            eye_fill = (255, 230, 120)  # pale amber

        for ex in (skull_cx - eye_dx, skull_cx + eye_dx):
            draw.ellipse([ex - eye_r, eye_y - eye_r, ex + eye_r, eye_y + eye_r], fill=eye_fill)

        # ── 4. Nasal cavity (small inverted-triangle) ────────────────────
        ns = max(1, skull_rx // 4)
        ny = skull_cy + skull_ry // 6
        draw.polygon(
            [(skull_cx, ny + ns), (skull_cx - ns, ny - ns // 2), (skull_cx + ns, ny - ns // 2)],
            fill=BLACK,
        )

        # ── 5. Teeth row ────────────────────────────────────────────────
        tooth_w   = max(1, skull_rx // 3)
        tooth_h   = max(1, skull_ry // 3)
        tooth_gap = tooth_w + max(1, tooth_w // 2)
        teeth_top = skull_cy + skull_ry - tooth_h

        num_teeth = max(2, skull_rx * 2 // tooth_gap)
        teeth_total = num_teeth * tooth_gap - (tooth_gap - tooth_w)
        teeth_x0 = skull_cx - teeth_total // 2

        for i in range(num_teeth):
            tx = teeth_x0 + i * tooth_gap
            draw.rectangle([tx, teeth_top, tx + tooth_w, skull_cy + skull_ry], fill=BLACK)

        # ── 6. Optional win-text overlay ────────────────────────────────
        if self.show_text:
            text_y = h - self.font_size - 2
            self._draw_small_text(draw, state.win_text, 1, text_y, BLACK)    # shadow
            self._draw_small_text(draw, state.win_text, 0, text_y - 1, skull_color)

        return img

    # Logo paths follow the LEDMatrix assets convention:
    # assets/sports/{sport}_logos/{ABBR}.png  (relative to CWD = LEDMatrix root)
    _LOGO_DIRS: Dict[str, str] = {
        "mlb": "assets/sports/mlb_logos",
        "nfl": "assets/sports/nfl_logos",
        "nba": "assets/sports/nba_logos",
        "nhl": "assets/sports/nhl_logos",
    }

    def _load_team_logo(self, state: "_TeamState") -> Optional[Image.Image]:
        """
        Load (and cache) the team logo from the LEDMatrix assets directory.

        The logo is resized to fit the display height while preserving aspect
        ratio, then cached so it is only read from disk once per team.

        Returns None if the logo file is missing or cannot be opened.
        """
        cache_key = f"{state.sport}_{state.abbreviation}"
        if cache_key in self._logo_cache:
            return self._logo_cache[cache_key]

        logo_dir = self._LOGO_DIRS.get(state.sport, f"assets/sports/{state.sport}_logos")
        logo_path = Path(logo_dir) / f"{state.abbreviation}.png"

        logo: Optional[Image.Image] = None
        try:
            if logo_path.exists():
                raw = Image.open(logo_path).convert("RGBA")
                # Crop transparent border for a tighter fit
                bbox = raw.getbbox()
                if bbox:
                    raw = raw.crop(bbox)
                target_h = self.display_height - 2
                ratio = target_h / max(raw.height, 1)
                target_w = max(1, int(raw.width * ratio))
                logo = raw.resize((target_w, target_h), Image.Resampling.LANCZOS)
                self.logger.info(
                    "[%s] Loaded team logo from %s (%dx%d)",
                    state.abbreviation, logo_path, target_w, target_h,
                )
            else:
                self.logger.warning(
                    "[%s] Team logo not found at %s", state.abbreviation, logo_path
                )
        except Exception as exc:
            self.logger.warning("[%s] Failed to load team logo: %s", state.abbreviation, exc)

        self._logo_cache[cache_key] = logo
        return logo

    def _render_logo_frame(
        self, state: "_TeamState", w: int, h: int, frame_idx: int, num_frames: int
    ) -> Image.Image:
        """
        Render a single frame that shows the team logo with pulsing brightness.

        The logo is placed on the left side of the canvas; win text and score
        are drawn to the right.  Falls back to a solid primary-colour rectangle
        labelled with the team abbreviation when no logo file is found.
        """
        img = Image.new("RGB", (w, h), BLACK)
        draw = ImageDraw.Draw(img)

        phase = (2 * math.pi * frame_idx) / num_frames
        brightness = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(phase))

        logo = self._load_team_logo(state)
        if logo:
            # Dim logo channels to match the pulse brightness
            r, g, b, a = logo.split()
            def _dim_ch(ch: Any) -> Any:
                return ch.point(lambda x: int(x * brightness))
            bright_logo = Image.merge("RGBA", (_dim_ch(r), _dim_ch(g), _dim_ch(b), a))
            paste_x = 1
            paste_y = (h - bright_logo.height) // 2
            img.paste(bright_logo, (paste_x, paste_y), bright_logo)
            text_x = bright_logo.width + 4
        else:
            # Fallback: colored square with abbreviation
            sq = min(h - 4, 20)
            sq_color = tuple(max(0, min(255, int(c * brightness))) for c in state.primary_color)
            draw.rectangle([2, (h - sq) // 2, 2 + sq, (h + sq) // 2], fill=sq_color)
            abbr = state.abbreviation
            bbox = draw.textbbox((0, 0), abbr, font=self.font)
            abbr_w = bbox[2] - bbox[0]
            self._draw_small_text(draw, abbr, 2 + (sq - abbr_w) // 2, (h - self.font_size) // 2, BLACK)
            text_x = sq + 5

        # Win text and score to the right of the logo
        text_color = tuple(max(0, min(255, int(c * brightness))) for c in state.primary_color)
        if self.show_text and state.win_text:
            self._draw_small_text(draw, state.win_text, text_x, 2, text_color)

        if self.show_score and state.win_info:
            team_score = state.win_info.get("team_score", 0)
            opp_score  = state.win_info.get("opp_score", 0)
            opp_abbr   = state.win_info.get("opp_abbr", "OPP")
            line1 = f"{state.abbreviation} {team_score}"
            line2 = f"{opp_abbr} {opp_score}"
            self._draw_small_text(draw, line1, text_x, h // 2 - self.font_size, WHITE)
            self._draw_small_text(draw, line2, text_x, h // 2 + 1, DIM_RED)

        return img

    # ------------------------------------------------------------------
    # Overlay drawing
    # ------------------------------------------------------------------

    def _draw_overlays(self, state: _TeamState, img: Image.Image) -> None:
        """Draw flashing win text and score overlays onto a copied frame."""
        draw = ImageDraw.Draw(img)
        w, _h = img.size

        # Win text — flashing, top-left
        if self.show_text and state.flash_on:
            # Black shadow for readability over the GIF
            self._draw_small_text(draw, state.win_text, 2, 2, BLACK)
            self._draw_small_text(draw, state.win_text, 1, 1, state.primary_color)

        # Score — right-aligned, top-right
        if self.show_score and state.win_info:
            team_abbr  = state.win_info.get("team_abbr",  state.abbreviation)
            opp_abbr   = state.win_info.get("opp_abbr",   "OPP")
            team_score = state.win_info.get("team_score", 0)
            opp_score  = state.win_info.get("opp_score",  0)

            line1 = f"{team_abbr} {team_score}"
            line2 = f"{opp_abbr} {opp_score}"

            b1 = draw.textbbox((0, 0), line1, font=self.font)
            b2 = draw.textbbox((0, 0), line2, font=self.font)
            x1 = w - (b1[2] - b1[0]) - 1
            x2 = w - (b2[2] - b2[0]) - 1

            # Shadows
            self._draw_small_text(draw, line1, x1 + 1, 2, BLACK)
            self._draw_small_text(draw, line2, x2 + 1, self.font_size + 3, BLACK)
            # Team score in white, opponent score in dim red
            self._draw_small_text(draw, line1, x1, 1, WHITE)
            self._draw_small_text(draw, line2, x2, self.font_size + 2, DIM_RED)

    def _draw_small_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        x: int,
        y: int,
        color: Tuple[int, int, int],
    ) -> None:
        try:
            draw.text((x, y), text, font=self.font, fill=color)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _celebrating_teams(self) -> List[_TeamState]:
        """Return all teams currently in an active celebration window."""
        return [s for s in self._team_states.values() if s.celebrating]

    def _resolve_simulate_target(self) -> Optional["_TeamState"]:
        """Return the _TeamState for the configured simulate_team slot (1-5), or the first active team if 0."""
        if not self._team_states:
            return None
        slot = self.simulate_team
        if slot == 0:
            return next(iter(self._team_states.values()))
        state = self._team_states.get(slot)
        if state is None:
            self.logger.warning("simulate_team slot %d not found (active slots: %s)", slot, list(self._team_states.keys()))
        return state

    def _trigger_simulation(self, state: _TeamState) -> None:
        """Activate a simulated win for testing."""
        state.celebrating = True
        state.win_expires_at = datetime.now() + timedelta(hours=self.celebration_hours)
        state.last_win_score = "7-4"
        state.win_info = {
            "team_abbr":  state.abbreviation,
            "opp_abbr":   "SIM",
            "team_score": 7,
            "opp_score":  4,
        }
        state.live_priority_fired = False
        state.display_logged = False
        self._build_frames(state)
        self.logger.info(
            "[%s] Simulated win activated — celebrating for %.1f hours",
            state.abbreviation, self.celebration_hours,
        )

    def _check_all_expiry(self) -> None:
        """Deactivate celebrations whose configured window has passed."""
        now = datetime.now()
        for state in self._team_states.values():
            if state.celebrating and state.win_expires_at and now >= state.win_expires_at:
                state.celebrating = False
                self.logger.info("[%s] Celebration window expired", state.abbreviation)

    # ------------------------------------------------------------------
    # Update — ESPN scoreboard polling
    # ------------------------------------------------------------------

    def update(self) -> None:
        """Poll ESPN scoreboards for each configured sport."""
        if self.simulate_win:
            target = self._resolve_simulate_target()
            if target and not target.celebrating:
                self._trigger_simulation(target)
            self._check_all_expiry()
            return

        # Group teams by sport so we make at most one API call per endpoint
        teams_by_sport: Dict[str, List[_TeamState]] = {}
        for state in self._team_states.values():
            teams_by_sport.setdefault(state.sport, []).append(state)

        today = datetime.now().date()

        for sport, sport_states in teams_by_sport.items():
            # Skip the API call when every team in this sport has confirmed no game today
            if all(s.game_today is False and s.game_date == today for s in sport_states):
                self.logger.debug(
                    "[%s] No game today for any tracked team — skipping ESPN poll", sport.upper()
                )
                continue

            # Per-sport throttle
            last_update = self._last_sport_update.get(sport)
            if last_update:
                elapsed = (datetime.now() - last_update).total_seconds()
                if elapsed < self.update_interval_seconds:
                    self.logger.debug(
                        "[%s] Skipping update — last check %.0fs ago", sport.upper(), elapsed
                    )
                    continue

            endpoint = ESPN_ENDPOINTS.get(sport)
            if not endpoint:
                self.logger.warning("No ESPN endpoint configured for sport '%s'", sport)
                continue

            try:
                data = self.api_helper.get(url=endpoint)
                if data:
                    self._process_scoreboard(data, sport_states, sport)
                else:
                    self.logger.warning("[%s] No data returned from ESPN", sport.upper())
            except Exception as exc:
                self.logger.error(
                    "[%s] Error during update: %s", sport.upper(), exc, exc_info=True
                )

            self._last_sport_update[sport] = datetime.now()

        self._check_all_expiry()

    @staticmethod
    def _event_local_date(event_date_str: str) -> Optional[date]:
        """
        Parse an ESPN ISO-8601 UTC event date string to the local calendar date.
        ESPN uses "2024-04-15T23:10:00Z" — Python < 3.11 rejects the "Z" suffix,
        so we normalise it to "+00:00" before parsing.
        """
        if not event_date_str:
            return None
        try:
            normalized = event_date_str.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized).astimezone().date()
        except (ValueError, TypeError):
            return None

    def _process_scoreboard(
        self,
        data: Dict[str, Any],
        sport_states: List[_TeamState],
        sport: str,
    ) -> None:
        """
        Scan a single sport's scoreboard and detect wins for all tracked teams.
        Each team's celebration state is updated independently so simultaneous
        wins (e.g. two teams win on the same day) both trigger celebrations.
        """
        events = data.get("events", [])
        today = datetime.now().date()

        # Track whether each abbreviation appeared in today's schedule at all
        game_exists_for: Dict[str, bool] = {s.abbreviation: False for s in sport_states}

        for event in events:
            competitions = event.get("competitions", [])
            if not competitions:
                continue

            # Skip events from a previous calendar day still on the feed
            event_date = self._event_local_date(event.get("date", ""))
            if event_date is not None and event_date < today:
                continue

            competition = competitions[0]
            competitors = competition.get("competitors", [])
            home = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away = next((c for c in competitors if c.get("homeAway") == "away"), None)
            if not home or not away:
                continue

            home_abbr = home.get("team", {}).get("abbreviation", "")
            away_abbr = away.get("team", {}).get("abbreviation", "")

            for state in sport_states:
                abbr = state.abbreviation
                if abbr not in (home_abbr, away_abbr):
                    continue

                # Team has at least one game on today's schedule
                game_exists_for[abbr] = True

                # Only process final (post-game) events for win detection
                if competition.get("status", {}).get("type", {}).get("state", "") != "post":
                    continue

                home_score = int(home.get("score", 0) or 0)
                away_score = int(away.get("score", 0) or 0)

                if home_abbr == abbr:
                    won = home_score > away_score
                    team_score, opp_score, opp_abbr = home_score, away_score, away_abbr
                else:
                    won = away_score > home_score
                    team_score, opp_score, opp_abbr = away_score, home_score, home_abbr

                if not won:
                    self.logger.debug(
                        "[%s] Lost to %s (%d-%d)", abbr, opp_abbr, team_score, opp_score
                    )
                    continue

                score_str = f"{team_score}-{opp_score}"
                # Guard ensures double-headers trigger a new celebration per game
                if state.last_win_score != score_str:
                    state.celebrating = True
                    state.win_expires_at = datetime.now() + timedelta(
                        hours=self.celebration_hours
                    )
                    state.last_win_score = score_str
                    state.win_info = {
                        "team_abbr":  abbr,
                        "opp_abbr":   opp_abbr,
                        "team_score": team_score,
                        "opp_score":  opp_score,
                    }
                    state.live_priority_fired = False
                    state.display_logged = False
                    self._build_frames(state)
                    self.logger.info(
                        "[%s] Win detected! %d-%d vs %s. Celebrating for %.1f hours.",
                        abbr, team_score, opp_score, opp_abbr, self.celebration_hours,
                    )
                # Do NOT break — check remaining sport_states for this event
                # (edge case: same team listed twice, or a future dual-abbr scenario)

        # Update the no-game-today flags so update() can skip the API tomorrow
        for state in sport_states:
            state.game_today = game_exists_for[state.abbreviation]
            state.game_date = today
            if not state.game_today:
                self.logger.info(
                    "[%s] No %s game found in today's schedule (%s)",
                    state.abbreviation, sport.upper(), today,
                )

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def display(self, force_clear: bool = False) -> bool:
        """
        Render the current celebrating team's animation frame to the LED matrix.

        Returns False when no team is celebrating so the display controller
        skips this plugin and advances to the next in the rotation.

        When multiple teams are celebrating, rotates between them every
        team_display_duration seconds (wall-clock time).
        """
        self._check_all_expiry()
        active = self._celebrating_teams()
        if not active:
            return False

        # Ensure every active team has frames (reload if lost)
        for s in active:
            if not s.frames:
                self._build_frames(s)
        active = [s for s in active if s.frames]
        if not active:
            return False

        now = time.monotonic()

        # Determine which team to show right now
        current = self._team_states.get(self._current_team_slot)
        if current is None or not current.celebrating:
            # Start with the first celebrating team
            current = active[0]
            self._current_team_slot = next(k for k, v in self._team_states.items() if v is current)
            self._current_team_start = now
        elif len(active) > 1 and (now - self._current_team_start) >= self.team_display_duration:
            # Rotate to the next celebrating team
            idx = next(
                (i for i, s in enumerate(active) if s is current),
                0,
            )
            prev_abbr = current.abbreviation
            current = active[(idx + 1) % len(active)]
            self._current_team_slot = next(k for k, v in self._team_states.items() if v is current)
            self._current_team_start = now
            self.logger.info(
                "[%s] Rotating celebration display from %s (%d team(s) active)",
                current.abbreviation, prev_abbr, len(active),
            )

        # Signal that this win has been displayed (disables live-priority re-takeover)
        current.live_priority_fired = True

        # Log the first time this celebration actually renders on the matrix
        if not current.display_logged:
            self.logger.info(
                "[%s] Celebration now displaying on matrix (win: %s-%s vs %s)",
                current.abbreviation,
                current.win_info.get("team_score", "?"),
                current.win_info.get("opp_score", "?"),
                current.win_info.get("opp_abbr", "?"),
            )
            current.display_logged = True

        try:
            # Advance GIF frame based on per-frame duration
            if current.frame_durations:
                frame_duration = current.frame_durations[
                    current.frame_index % len(current.frame_durations)
                ]
            else:
                frame_duration = 1.0 / max(self.animation_fps, 1.0)

            if now - current.last_frame_time >= frame_duration:
                current.frame_index = (current.frame_index + 1) % len(current.frames)
                current.last_frame_time = now

            # Advance flash state
            if now - current.flash_last_toggle >= self._flash_period:
                current.flash_on = not current.flash_on
                current.flash_last_toggle = now

            # Composite overlays onto a copy of the current frame
            frame = current.frames[current.frame_index].copy()
            self._draw_overlays(current, frame)

            self.display_manager.image = frame
            self.display_manager.update_display()
            return True

        except Exception as exc:
            self.logger.error(
                "[%s] Error in display(): %s", current.abbreviation, exc, exc_info=True
            )
            return False

    # ------------------------------------------------------------------
    # Live priority — immediate takeover after a new win
    # ------------------------------------------------------------------

    def has_live_priority(self) -> bool:
        """Required by display controller to interrupt Vegas mode on a new win."""
        return self.has_live_content()

    def has_live_content(self) -> bool:
        """
        Returns True if any team has a new win that hasn't been displayed yet.
        Triggers an immediate display takeover; resets once display() is called.
        """
        return any(
            s.celebrating and not s.live_priority_fired
            for s in self._team_states.values()
        )

    def get_live_modes(self) -> List[str]:
        return ["win_celebration"]

    # ------------------------------------------------------------------
    # Vegas scroll support
    # ------------------------------------------------------------------

    def get_vegas_content_type(self) -> str:
        # Always excluded from the Vegas stream; celebration is driven entirely
        # by the live-priority mechanism so the stream never holds a stale
        # STATIC placeholder that corrupts scroll geometry on resume.
        return "none"

    def get_vegas_display_mode(self) -> VegasDisplayMode:
        return VegasDisplayMode.FIXED_SEGMENT

    def get_vegas_content(self) -> Optional[Image.Image]:
        return None

    # ------------------------------------------------------------------
    # Configuration & lifecycle
    # ------------------------------------------------------------------

    def validate_config(self) -> bool:
        if not super().validate_config():
            return False

        if not self._teams_config:
            self.logger.error(
                "No teams configured — set an abbreviation in at least one of team1–team5"
            )
            return False

        hours = self.config.get("celebration_hours", 1.0)
        try:
            if not (0 < float(hours) <= 24):
                self.logger.error("celebration_hours must be between 0 and 24")
                return False
        except (TypeError, ValueError):
            self.logger.error("celebration_hours must be a number")
            return False

        fps = self.config.get("animation_fps", 12.0)
        try:
            if not (0 < float(fps) <= 60):
                self.logger.error("animation_fps must be between 0 and 60")
                return False
        except (TypeError, ValueError):
            self.logger.error("animation_fps must be a number")
            return False

        return True

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        was_simulating = self.simulate_win
        super().on_config_change(new_config)
        self._load_config()
        self._load_fonts()
        self._logo_cache.clear()  # team slots, abbreviations, or sports may have changed

        # Rebuild team states from the updated config, preserving existing win state
        new_states: Dict[int, _TeamState] = {}
        for slot, team_cfg in enumerate(self._teams_config, 1):
            state = _TeamState(team_cfg)
            if not state.abbreviation:
                continue
            old = self._team_states.get(slot)
            if old:
                state.celebrating = old.celebrating
                state.win_expires_at = old.win_expires_at
                state.last_win_score = old.last_win_score
                state.win_info = old.win_info
                state.game_today = old.game_today
                state.game_date = old.game_date
            new_states[slot] = state

        self._team_states = new_states

        for state in self._team_states.values():
            self._build_frames(state)

        if self.simulate_win and not was_simulating:
            target = self._resolve_simulate_target()
            if target:
                self._trigger_simulation(target)
        elif not self.simulate_win and was_simulating:
            for state in self._team_states.values():
                state.celebrating = False
            self.logger.info("Simulation cancelled")

        self.logger.info("Configuration updated")

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info["teams"] = {
            f"slot{slot}_{s.abbreviation}": {
                "celebrating":    s.celebrating,
                "win_expires_at": s.win_expires_at.isoformat() if s.win_expires_at else None,
                "last_win_score": s.last_win_score,
                "sport":          s.sport,
                "game_today":     s.game_today,
            }
            for slot, s in self._team_states.items()
        }
        info["current_team_slot"] = self._current_team_slot
        return info

    def cleanup(self) -> None:
        for state in self._team_states.values():
            state.frames = []
            state.frame_durations = []
            state.win_info = {}
            state.celebrating = False
            state.live_priority_fired = False
        self._logo_cache.clear()
        self.logger.info("Win Celebration plugin cleaned up")

"""
Central configuration for the CS2 prop discrepancy scanner.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- PrizePicks ---
# Public partner API, no key required. league_id 265 = CS2.
PRIZEPICKS_BASE_URL = "https://partner-api.prizepicks.com/projections"
PRIZEPICKS_CS2_LEAGUE_ID = int(os.environ.get("PRIZEPICKS_CS2_LEAGUE_ID", "265"))
PRIZEPICKS_PER_PAGE = 250

# --- Underdog ---
# Public pick'em catalog used by the Underdog website. No key required.
# The payload is large (~10MB+), so this timeout is higher than PrizePicks.
UNDERDOG_TIMEOUT_SECONDS = int(os.environ.get("UNDERDOG_TIMEOUT_SECONDS", "90"))

# --- Chalkboard ---
# Chalkboard does offer CS2 at major events, but there is no public board API
# analogous to PrizePicks/Underdog. Keep the adapter stubbed until one exists.
CHALKBOARD_ENABLED = os.environ.get("CHALKBOARD_ENABLED", "false").lower() in (
    "1", "true", "yes",
)

# --- Matching / discrepancy thresholds ---
DEFAULT_LINE_DIFF_THRESHOLD = 0.5
PLAYER_NAME_FUZZY_CUTOFF = 0.82

REQUEST_TIMEOUT_SECONDS = 20

# --- Polymarket ---
# Public Gamma API. series 10310 = CS2 match events.
POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com"
POLYMARKET_CS2_SERIES_ID = int(os.environ.get("POLYMARKET_CS2_SERIES_ID", "10310"))
POLYMARKET_CACHE_SECONDS = int(os.environ.get("POLYMARKET_CACHE_SECONDS", "120"))

# Canonical public URL for SEO / sitemap. Override if you add a custom domain.
PUBLIC_SITE_URL = os.environ.get(
    "PUBLIC_SITE_URL",
    "https://cs2-prop-scanner.onrender.com",
).rstrip("/")

# --- BallDontLie CS2 (player DB / map stats). Key in .env, never commit it. ---
BALLDONTLIE_API_KEY = os.environ.get("BALLDONTLIE_API_KEY", "").strip()
BALLDONTLIE_BASE_URL = "https://api.balldontlie.io/cs/v1"
# Spacing between burst calls. Rate-limit headers control the 5/min window.
# Values >= 3 are treated as the old "13s trial spacer" and ignored.
_bdl_gap = float(os.environ.get("BALLDONTLIE_MIN_INTERVAL", "0.08"))
BALLDONTLIE_MIN_INTERVAL = 0.08 if _bdl_gap >= 3 else max(0.05, _bdl_gap)

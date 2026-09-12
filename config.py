"""
Lead Pipeline — Central Configuration

Loads environment variables and defines all budget caps, default targets,
blocklists, and shared constants used across pipeline stages.
"""

import os
import re
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root with override=True so .env strictly takes precedence
_project_root = Path(__file__).parent
_env_path = _project_root / ".env"
load_dotenv(_env_path, override=True)

# ============================================================
# Database
# ============================================================
_data_dir = _project_root / "data"
_data_dir.mkdir(parents=True, exist_ok=True)

_raw_db = os.getenv("DB_PATH", "data/pipeline.db")
_candidate_path = Path(_raw_db)
if not _candidate_path.is_absolute():
    # If configured as 'pipeline.db' but data/pipeline.db exists, prefer data/pipeline.db
    if _candidate_path == Path("pipeline.db") and (_data_dir / "pipeline.db").exists():
        _candidate_path = _data_dir / "pipeline.db"
    else:
        _candidate_path = _project_root / _candidate_path

DB_PATH = str(_candidate_path)
DATABASE_URL = os.getenv("DATABASE_URL", "")  # PostgreSQL (optional)

# ============================================================
# Google Places API (New)
# ============================================================
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
PLACES_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELDMASK = (
    "places.id,"
    "places.displayName,"
    "places.websiteUri,"
    "places.formattedAddress,"
    "places.nationalPhoneNumber,"
    "nextPageToken"
)
PLACES_BUDGET_HARD_CAP_USD = float(os.getenv("PLACES_BUDGET_CAP", "10.00"))
PLACES_COST_PER_REQUEST = 0.032  # Basic Data Tier: $32 / 1,000 requests
PLACES_MAX_REQUESTS_PER_RUN = int(os.getenv("PLACES_MAX_REQUESTS", "2000"))
PLACES_GLOBAL_LEAD_CAP = int(os.getenv("PLACES_GLOBAL_LEAD_CAP", "3000"))
PLACES_PER_CITY_TARGET = int(os.getenv("PLACES_PER_CITY_TARGET", "50"))
PLACES_PAGE_SIZE = 20

# Strict activity recency limit (days)
MAX_ACTIVITY_DAYS = int(os.getenv("MAX_ACTIVITY_DAYS", "30"))

# ============================================================
# Search Engine Provider (Stage 3: LinkedIn Resolution)
# Options: "serper" (recommended: 2,500 free Google searches), "google", "bing"
# ============================================================
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "serper").lower()

# Serper.dev (Google Search API — 2,500 free queries, zero CC needed)
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPER_ENDPOINT = "https://google.serper.dev/search"

# Google Custom Search JSON API (requires legacy grandfathered project)
GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY", "") or GOOGLE_PLACES_API_KEY
GOOGLE_SEARCH_ENGINE_ID = os.getenv("GOOGLE_SEARCH_ENGINE_ID", "")  # CX ID
GOOGLE_SEARCH_ENDPOINT = "https://customsearch.googleapis.com/customsearch/v1"

# Azure Bing Web Search API (fallback)
AZURE_BING_SEARCH_KEY = os.getenv("AZURE_BING_SEARCH_KEY", "")
BING_ENDPOINT = os.getenv("BING_ENDPOINT", "https://api.bing.microsoft.com/v7.0/search")
SEARCH_COST_PER_REQUEST = 0.005
BING_COST_PER_REQUEST = SEARCH_COST_PER_REQUEST

# ============================================================
# Azure OpenAI
# ============================================================
_raw_openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_ENDPOINT = re.sub(r"/openai/?.*$", "", _raw_openai_endpoint).rstrip("/")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

# ============================================================
# Default Target Cities (Top 50 US Metro Markets)
# ============================================================
DEFAULT_CITIES = [
    "Austin, TX",
    "Miami, FL",
    "New York, NY",
    "Chicago, IL",
    "Denver, CO",
    "Atlanta, GA",
    "Dallas, TX",
    "Boston, MA",
    "San Francisco, CA",
    "Seattle, WA",
    "Los Angeles, CA",
    "San Diego, CA",
    "Houston, TX",
    "Phoenix, AZ",
    "Philadelphia, PA",
    "San Antonio, TX",
    "San Jose, CA",
    "Charlotte, NC",
    "Columbus, OH",
    "Indianapolis, IN",
    "Nashville, TN",
    "Portland, OR",
    "Las Vegas, NV",
    "Memphis, TN",
    "Louisville, KY",
    "Baltimore, MD",
    "Milwaukee, WI",
    "Albuquerque, NM",
    "Tucson, AZ",
    "Fresno, CA",
    "Sacramento, CA",
    "Mesa, AZ",
    "Kansas City, MO",
    "Omaha, NE",
    "Colorado Springs, CO",
    "Raleigh, NC",
    "Long Beach, CA",
    "Virginia Beach, VA",
    "Oakland, CA",
    "Minneapolis, MN",
    "Tampa, FL",
    "Tulsa, OK",
    "Arlington, TX",
    "New Orleans, LA",
    "Wichita, KS",
    "Cleveland, OH",
    "Detroit, MI",
    "Pittsburgh, PA",
    "Salt Lake City, UT",
    "Orlando, FL",
    "Cincinnati, OH",
    "St. Louis, MO",
    "Richmond, VA",
    "Jacksonville, FL",
    "Fort Worth, TX",
    "Oklahoma City, OK",
    "Honolulu, HI",
    "Anaheim, CA",
    "Santa Ana, CA",
    "Riverside, CA",
    "Lexington, KY",
    "Stockton, CA",
    "Henderson, NV",
    "Saint Paul, MN",
    "Greensboro, NC",
    "Plano, TX",
    "Durham, NC",
    "Buffalo, NY",
    "Jersey City, NJ",
    "Chandler, AZ",
    "Madison, WI",
    "Reno, NV",
    "Winston-Salem, NC",
    "Scottsdale, AZ",
    "Irving, TX",
    "Fremont, CA",
    "Baton Rouge, LA",
    "Boise, ID",
    "Spokane, WA",
    "Birmingham, AL",
    "Des Moines, IA",
    "Rochester, NY",
    "Tacoma, WA",
]

# ============================================================
# Streamlined Search Queries (Eliminate in-city duplicate waste)
# ============================================================
DEFAULT_QUERIES = [
    "marketing agency",
    "digital advertising agency",
]

# ============================================================
# Domain Blocklist — Filter aggregator/social pages before DB insert
# ============================================================
DOMAIN_BLOCKLIST = frozenset({
    # Social Media
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "tiktok.com", "pinterest.com", "reddit.com", "linkedin.com",
    # Directories & Aggregators
    "yelp.com", "clutch.co", "bbb.org", "thumbtack.com", "bark.com",
    "yellowpages.com", "manta.com", "g2.com", "capterra.com",
    "crunchbase.com", "glassdoor.com", "indeed.com",
    # Freelance Marketplaces
    "upwork.com", "fiverr.com", "toptal.com",
    # URL Shorteners & Link-in-Bio
    "linktr.ee", "bit.ly", "tinyurl.com", "lnkd.in",
    # Big Tech (false positives)
    "google.com", "apple.com", "microsoft.com", "amazon.com",
})

# ============================================================
# AI Note Personalization — Jargon Blacklist
# ============================================================
JARGON_BLACKLIST = [
    "synergy", "elevate", "game-changer", "hop on a call",
    "free audit", "streamline", "leverage", "circle back",
    "deep dive", "low-hanging fruit", "move the needle",
    "touch base", "bandwidth", "paradigm", "disrupt",
    "best-in-class", "world-class", "cutting-edge",
    "revolutionize", "transform your", "unlock",
    "schedule a call", "book a demo", "15-minute",
]

# ============================================================
# Portal & Stage 6: Mobile Queue Server
# ============================================================
QUEUE_DAILY_LIMIT = int(os.getenv("QUEUE_DAILY_LIMIT", "15"))
QUEUE_HOST = os.getenv("QUEUE_HOST", "0.0.0.0")
QUEUE_PORT = int(os.getenv("PORT", os.getenv("QUEUE_PORT", "8000")))  # Azure App Service passes $PORT
PORTAL_AUTH_USERNAME = os.getenv("PORTAL_AUTH_USERNAME", os.getenv("QUEUE_AUTH_USERNAME", "admin"))
PORTAL_AUTH_PASSWORD = os.getenv("PORTAL_AUTH_PASSWORD", os.getenv("QUEUE_AUTH_PASSWORD", "admin123"))
QUEUE_AUTH_USERNAME = PORTAL_AUTH_USERNAME
QUEUE_AUTH_PASSWORD = PORTAL_AUTH_PASSWORD
PORTAL_SECRET_KEY = os.getenv("PORTAL_SECRET_KEY", "lead-gen-engine-secret-key-2026-prod-azure")

# Follower filtering thresholds (Sweet spot: 500+ connections to 5k followers)
FOLLOWER_SWEET_SPOT_MIN = int(os.getenv("FOLLOWER_SWEET_SPOT_MIN", "500"))
FOLLOWER_SWEET_SPOT_MAX = int(os.getenv("FOLLOWER_SWEET_SPOT_MAX", "5000"))
FOLLOWER_EXCLUDE_LIMIT = int(os.getenv("FOLLOWER_EXCLUDE_LIMIT", "5000"))

# ============================================================
# LinkedIn Resolution — Name Variant Mapping
# ============================================================
NAME_VARIANTS = {
    "alex": "alexander", "mike": "michael", "dan": "daniel",
    "dave": "david", "rob": "robert", "bob": "robert",
    "jim": "james", "joe": "joseph", "tom": "thomas",
    "bill": "william", "will": "william", "chris": "christopher",
    "matt": "matthew", "nick": "nicholas", "tony": "anthony",
    "steve": "steven", "ben": "benjamin", "sam": "samuel",
    "pat": "patrick", "rick": "richard", "dick": "richard",
    "ted": "theodore", "ed": "edward", "tim": "timothy",
    "ken": "kenneth", "jeff": "jeffrey", "greg": "gregory",
    "andy": "andrew", "jon": "jonathan", "phil": "philip",
    "liz": "elizabeth", "beth": "elizabeth", "kate": "katherine",
    "jen": "jennifer", "jess": "jessica", "meg": "megan",
}

# ============================================================
# Lead Status ENUM Constants
# ============================================================
class Status:
    DISCOVERED = "DISCOVERED"
    ENRICHED = "ENRICHED"
    DEAD_WEBSITE = "DEAD_WEBSITE"
    NO_LEADERSHIP = "NO_LEADERSHIP"
    LINKEDIN_RESOLVED = "LINKEDIN_RESOLVED"
    NO_LINKEDIN = "NO_LINKEDIN"
    QUALIFIED_ACTIVE = "QUALIFIED_ACTIVE"
    DORMANT_REJECT = "DORMANT_REJECT"
    QUEUED = "QUEUED"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    EXPIRED_4W = "EXPIRED_4W"

# Valid state transitions
VALID_TRANSITIONS = {
    Status.DISCOVERED: {Status.ENRICHED, Status.LINKEDIN_RESOLVED, Status.DEAD_WEBSITE, Status.NO_LEADERSHIP},
    Status.ENRICHED: {Status.LINKEDIN_RESOLVED, Status.NO_LINKEDIN},
    Status.NO_LEADERSHIP: {Status.LINKEDIN_RESOLVED, Status.NO_LINKEDIN},
    Status.NO_LINKEDIN: {Status.LINKEDIN_RESOLVED},
    Status.LINKEDIN_RESOLVED: {Status.QUALIFIED_ACTIVE, Status.DORMANT_REJECT},
    Status.QUALIFIED_ACTIVE: {Status.QUEUED},
    Status.QUEUED: {Status.SENT},
    Status.SENT: {Status.ACCEPTED, Status.EXPIRED_4W},
}

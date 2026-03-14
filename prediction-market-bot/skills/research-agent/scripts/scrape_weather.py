"""
Step 1b: Fetch NWS (National Weather Service) forecast data for weather markets.

Uses api.weather.gov — the SAME data source Kalshi uses for settlement via the
National Weather Service Daily Climatological Report (CLI product). No API key needed.

Pipeline:
  1. GET api.weather.gov/points/{lat},{lon} → NWS grid office + (gridX, gridY)
  2. GET /gridpoints/{office}/{x},{y} → quantitative gridpoint data (temp, precip, snow, wind)
  3. Aggregate hourly values into daily stats for the target date
  4. Fall back to Open-Meteo if NWS is unreachable

Reads:  data/scan_results.json
Writes: data/raw_weather.json
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
SCAN_RESULTS = DATA_DIR / "scan_results.json"
OUTPUT = DATA_DIR / "raw_weather.json"
NWS_GRID_CACHE_FILE = DATA_DIR / "nws_grid_cache.json"

NWS_BASE = "https://api.weather.gov"
NWS_HEADERS = {
    "User-Agent": "kalshi-prediction-bot/1.0 (prediction-market-research)",
    "Accept": "application/geo+json",
}

WEATHER_KEYWORDS = {
    "temperature", "temp", "degrees", "high", "low", "heat", "cold",
    "snow", "snowfall", "snowstorm", "blizzard",
    "rain", "rainfall", "precipitation", "precip", "shower", "storm",
    "wind", "gust",
    "humidity", "fog", "frost", "ice",
    "weather",
}

# US city name → (lat, lon)
CITY_CACHE = {
    "new york":       (40.7128, -74.0060),
    "new york city":  (40.7128, -74.0060),
    "nyc":            (40.7128, -74.0060),
    "ny":             (40.7128, -74.0060),
    "chicago":        (41.8781, -87.6298),
    "los angeles":    (34.0522, -118.2437),
    "la":             (34.0522, -118.2437),
    "houston":        (29.7604, -95.3698),
    "phoenix":        (33.4484, -112.0740),
    "philadelphia":   (39.9526, -75.1652),
    "san antonio":    (29.4241, -98.4936),
    "san diego":      (32.7157, -117.1611),
    "dallas":         (32.7767, -96.7970),
    "boston":         (42.3601, -71.0589),
    "seattle":        (47.6062, -122.3321),
    "denver":         (39.7392, -104.9903),
    "miami":          (25.7617, -80.1918),
    "atlanta":        (33.7490, -84.3880),
    "minneapolis":    (44.9778, -93.2650),
    "detroit":        (42.3314, -83.0458),
    "washington":     (38.9072, -77.0369),
    "washington dc":  (38.9072, -77.0369),
    "portland":       (45.5051, -122.6750),
    "las vegas":      (36.1699, -115.1398),
    "nashville":      (36.1627, -86.7816),
    "charlotte":      (35.2271, -80.8431),
    "baltimore":      (39.2904, -76.6122),
    "raleigh":        (35.7796, -78.6382),
    "kansas city":    (39.0997, -94.5786),
    "sacramento":     (38.5816, -121.4944),
    "tampa":          (27.9506, -82.4572),
    "pittsburgh":     (40.4406, -79.9959),
    "salt lake city": (40.7608, -111.8910),
    "new orleans":    (29.9511, -90.0715),
    "memphis":        (35.1495, -90.0490),
    "richmond":       (37.5407, -77.4360),
    "cincinnati":     (39.1031, -84.5120),
    "indianapolis":   (39.7684, -86.1581),
    "oklahoma city":  (35.4676, -97.5164),
    "st. louis":      (38.6270, -90.1994),
    "st louis":       (38.6270, -90.1994),
    "cleveland":      (41.4993, -81.6944),
    "omaha":          (41.2565, -95.9345),
    "anchorage":      (61.2181, -149.9003),
    "honolulu":       (21.3069, -157.8583),
    "buffalo":        (42.8864, -78.8784),
    "hartford":       (41.7658, -72.6851),
    "albany":         (42.6526, -73.7562),
    "rochester":      (43.1566, -77.6088),
}

CITY_PATTERN = re.compile(
    r'\bin\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:,\s*[A-Z]{2})?)',
)
DATE_PATTERN = re.compile(
    r'\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
    r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    r'\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# City extraction helpers
# ---------------------------------------------------------------------------

def is_weather_market(market: dict) -> bool:
    category = (market.get("category") or "").lower()
    title = (market.get("title") or "").lower()
    subtitle = (market.get("subtitle") or "").lower()
    if "weather" in category:
        return True
    return any(kw in f"{title} {subtitle}" for kw in WEATHER_KEYWORDS)


def extract_city(title: str) -> str | None:
    match = CITY_PATTERN.search(title)
    if match:
        city = match.group(1).lower().rstrip(",")
        city = re.sub(r',\s*[A-Z]{2}$', '', city).strip()
        return city
    # Scan city cache longest-match-first (handles "new york city" before "new york")
    title_lower = title.lower()
    for city in sorted(CITY_CACHE.keys(), key=len, reverse=True):
        # Use word-boundary style match: city must appear as a whole token
        if re.search(r'\b' + re.escape(city) + r'\b', title_lower):
            return city
    return None


def geocode_city(city: str) -> tuple[float, float] | None:
    city_lower = city.lower().strip()
    if city_lower in CITY_CACHE:
        return CITY_CACHE[city_lower]
    try:
        resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en", "format": "json"},
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                r = results[0]
                return (r["latitude"], r["longitude"])
    except Exception as e:
        logger.warning(f"Geocoding failed for '{city}': {e}")
    return None


def extract_forecast_date(market: dict) -> str | None:
    title = market.get("title", "")
    match = DATE_PATTERN.search(title)
    if match:
        month_str, day_str, year_str = match.group(1), match.group(2), match.group(3)
        year = int(year_str) if year_str else datetime.now(timezone.utc).year
        month_map = {
            "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
            "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
            "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
            "nov": 11, "november": 11, "dec": 12, "december": 12,
        }
        month = month_map.get(month_str.lower()[:3])
        if month:
            return f"{year}-{month:02d}-{int(day_str):02d}"
    close_time = market.get("close_time", "")
    if close_time:
        return close_time[:10]
    return None


# ---------------------------------------------------------------------------
# NWS grid cache (persisted to disk to avoid repeated /points lookups)
# ---------------------------------------------------------------------------

def _load_grid_cache() -> dict:
    if NWS_GRID_CACHE_FILE.exists():
        try:
            return json.loads(NWS_GRID_CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_grid_cache(cache: dict):
    DATA_DIR.mkdir(exist_ok=True)
    NWS_GRID_CACHE_FILE.write_text(json.dumps(cache, indent=2))


_grid_cache: dict = _load_grid_cache()


def get_nws_grid(lat: float, lon: float) -> dict | None:
    """Return NWS grid info for a lat/lon. Cached on disk."""
    key = f"{lat:.4f},{lon:.4f}"
    if key in _grid_cache:
        return _grid_cache[key]

    try:
        resp = requests.get(
            f"{NWS_BASE}/points/{lat:.4f},{lon:.4f}",
            headers=NWS_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"NWS /points returned {resp.status_code} for {key}")
            return None

        props = resp.json()["properties"]
        grid = {
            "office": props["gridId"],
            "gridX": props["gridX"],
            "gridY": props["gridY"],
        }
        _grid_cache[key] = grid
        _save_grid_cache(_grid_cache)
        return grid

    except Exception as e:
        logger.warning(f"NWS /points request failed for {key}: {e}")
        return None


# ---------------------------------------------------------------------------
# ISO 8601 duration parser
# ---------------------------------------------------------------------------

def parse_iso8601_duration(dur: str) -> timedelta:
    """Parse ISO 8601 duration like PT1H, PT6H, P1D, PT12H."""
    m = re.match(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?', dur)
    if not m:
        return timedelta(hours=1)
    days  = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    mins  = int(m.group(3) or 0)
    return timedelta(days=days, hours=hours, minutes=mins)


def parse_valid_time(valid_time_str: str) -> tuple[datetime, datetime]:
    """Return (start, end) for a NWS validTime string '{ISO}/{duration}'."""
    if "/" in valid_time_str:
        iso_part, dur_part = valid_time_str.split("/", 1)
        start = datetime.fromisoformat(iso_part)
        end   = start + parse_iso8601_duration(dur_part)
    else:
        start = datetime.fromisoformat(valid_time_str)
        end   = start + timedelta(hours=1)
    return start, end


# ---------------------------------------------------------------------------
# Unit converters
# ---------------------------------------------------------------------------

def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def mm_to_in(mm: float) -> float:
    return mm / 25.4


def kmh_to_mph(kmh: float) -> float:
    return kmh / 1.60934


# ---------------------------------------------------------------------------
# Gridpoint data aggregation
# ---------------------------------------------------------------------------

def aggregate_daily(values: list[dict], target_date: str, agg: str = "max",
                    convert=None) -> float | None:
    """
    Aggregate NWS gridpoint values for a target date (YYYY-MM-DD).

    agg: 'max', 'min', 'sum', or 'first'
    convert: optional unit-conversion callable applied to each raw value
    """
    target = datetime.fromisoformat(target_date).replace(tzinfo=timezone.utc)
    day_start = target.replace(hour=0, minute=0, second=0)
    day_end   = day_start + timedelta(days=1)

    collected = []
    for entry in values:
        if entry.get("value") is None:
            continue
        try:
            vstart, vend = parse_valid_time(entry["validTime"])
        except Exception:
            continue
        # Include value if its window overlaps with the target calendar day
        if vstart < day_end and vend > day_start:
            raw = float(entry["value"])
            val = convert(raw) if convert else raw
            collected.append(val)

    if not collected:
        return None

    if agg == "max":
        return max(collected)
    if agg == "min":
        return min(collected)
    if agg == "sum":
        return sum(collected)
    return collected[0]  # "first"


# ---------------------------------------------------------------------------
# Main NWS forecast fetcher
# ---------------------------------------------------------------------------

def fetch_nws_forecast(lat: float, lon: float, date_str: str) -> dict | None:
    """
    Fetch quantitative NWS gridpoint data for a target date.
    Returns a dict matching the same schema used by the rest of the pipeline.
    """
    grid = get_nws_grid(lat, lon)
    if not grid:
        return None

    office = grid["office"]
    gx, gy = grid["gridX"], grid["gridY"]
    url = f"{NWS_BASE}/gridpoints/{office}/{gx},{gy}"

    try:
        resp = requests.get(url, headers=NWS_HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"NWS gridpoints returned {resp.status_code} for {office}/{gx},{gy}")
            return None

        props = resp.json()["properties"]

        def get_field(name):
            return props.get(name, {}).get("values", [])

        # Daily high / low temperature (°C → °F)
        temp_high = aggregate_daily(get_field("maxTemperature"), date_str, "max", c_to_f)
        temp_low  = aggregate_daily(get_field("minTemperature"), date_str, "min", c_to_f)

        # If maxTemp/minTemp values aren't available for the date, fall back to hourly temps
        if temp_high is None:
            temp_high = aggregate_daily(get_field("temperature"), date_str, "max", c_to_f)
        if temp_low is None:
            temp_low = aggregate_daily(get_field("temperature"), date_str, "min", c_to_f)

        # Precipitation / snow (mm → inches)
        precip_in   = aggregate_daily(get_field("quantitativePrecipitation"), date_str, "sum", mm_to_in)
        snowfall_in = aggregate_daily(get_field("snowfallAmount"),            date_str, "sum", mm_to_in)
        # NWS doesn't split rain vs total — use total as rain if no snow
        rain_in = None
        if precip_in is not None and snowfall_in is not None:
            rain_in = max(0.0, precip_in - snowfall_in)
        elif precip_in is not None:
            rain_in = precip_in

        # Wind (km/h → mph)
        wind_max_mph  = aggregate_daily(get_field("windSpeed"), date_str, "max", kmh_to_mph)
        wind_gust_mph = aggregate_daily(get_field("windGust"),  date_str, "max", kmh_to_mph)

        # Precip probability (%)
        precip_prob = aggregate_daily(get_field("probabilityOfPrecipitation"), date_str, "max")

        return {
            "date":               date_str,
            "model":              "nws/gridpoint",
            "source":             "National Weather Service api.weather.gov",
            "nws_office":         office,
            "temp_high_f":        round(temp_high, 1) if temp_high is not None else None,
            "temp_low_f":         round(temp_low,  1) if temp_low  is not None else None,
            "precipitation_in":   round(precip_in,   3) if precip_in   is not None else None,
            "rain_in":            round(rain_in,     3) if rain_in     is not None else None,
            "snowfall_in":        round(snowfall_in, 3) if snowfall_in is not None else None,
            "wind_max_mph":       round(wind_max_mph,  1) if wind_max_mph  is not None else None,
            "wind_gust_max_mph":  round(wind_gust_mph, 1) if wind_gust_mph is not None else None,
            "precip_probability_pct": round(precip_prob, 1) if precip_prob is not None else None,
        }

    except Exception as e:
        logger.warning(f"NWS gridpoints request failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Open-Meteo fallback
# ---------------------------------------------------------------------------

def fetch_openmeteo_fallback(lat: float, lon: float, date_str: str) -> dict | None:
    """Fall back to Open-Meteo if NWS is unavailable (e.g. outside CONUS)."""
    FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
    try:
        resp = requests.get(
            FORECAST_URL,
            params={
                "latitude": lat, "longitude": lon,
                "daily": [
                    "temperature_2m_max", "temperature_2m_min",
                    "precipitation_sum", "rain_sum", "snowfall_sum",
                    "wind_speed_10m_max", "wind_gusts_10m_max",
                    "precipitation_probability_max",
                ],
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
                "timezone": "America/New_York",
                "forecast_days": 10,
                "models": "best_match",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return None

        data  = resp.json()
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        if not dates:
            return None

        if date_str not in dates:
            date_str = min(dates, key=lambda d: abs(
                (datetime.fromisoformat(d) - datetime.fromisoformat(date_str)).days
            ))

        idx = dates.index(date_str)

        def get_daily(key):
            vals = daily.get(key, [])
            return vals[idx] if idx < len(vals) else None

        return {
            "date":               date_str,
            "model":              "open-meteo/best_match",
            "source":             "Open-Meteo (fallback — not Kalshi settlement source)",
            "temp_high_f":        get_daily("temperature_2m_max"),
            "temp_low_f":         get_daily("temperature_2m_min"),
            "precipitation_in":   get_daily("precipitation_sum"),
            "rain_in":            get_daily("rain_sum"),
            "snowfall_in":        get_daily("snowfall_sum"),
            "wind_max_mph":       get_daily("wind_speed_10m_max"),
            "wind_gust_max_mph":  get_daily("wind_gusts_10m_max"),
            "precip_probability_pct": get_daily("precipitation_probability_max"),
        }
    except Exception as e:
        logger.warning(f"Open-Meteo fallback failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Forecast summary builder
# ---------------------------------------------------------------------------

def build_forecast_summary(forecast: dict, title: str) -> str:
    if not forecast:
        return "(weather forecast unavailable)"

    date   = forecast.get("date", "unknown date")
    source = forecast.get("source", "")
    parts  = [f"NWS forecast for {date} ({source}):"]

    hi = forecast.get("temp_high_f")
    lo = forecast.get("temp_low_f")
    if hi is not None and lo is not None:
        parts.append(f"Temperature: high {hi:.1f}°F / low {lo:.1f}°F")
    elif hi is not None:
        parts.append(f"Temperature: high {hi:.1f}°F")

    precip = forecast.get("precipitation_in")
    rain   = forecast.get("rain_in")
    snow   = forecast.get("snowfall_in")
    prob   = forecast.get("precip_probability_pct")
    if precip is not None:
        prob_str = f" ({prob:.0f}% chance)" if prob is not None else ""
        parts.append(f"Total precipitation: {precip:.2f} in{prob_str}")
    if rain is not None and rain > 0:
        parts.append(f"  Rain: {rain:.2f} in")
    if snow is not None and snow > 0:
        parts.append(f"  Snowfall: {snow:.2f} in")

    wind = forecast.get("wind_max_mph")
    gust = forecast.get("wind_gust_max_mph")
    if wind is not None:
        gust_str = f" (gusts to {gust:.0f} mph)" if gust else ""
        parts.append(f"Wind max: {wind:.0f} mph{gust_str}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

def scrape_weather(markets: list) -> dict:
    weather_markets = [m for m in markets if is_weather_market(m)]

    if not weather_markets:
        logger.info("No weather markets found in scan results")
        return {}

    logger.info(f"Found {len(weather_markets)} weather market(s)")
    results = {}

    for market in weather_markets:
        ticker = market["ticker"]
        title  = market.get("title", "")
        logger.info(f"  Processing: {title[:70]}")

        city = extract_city(title)
        if not city:
            logger.warning(f"    Could not extract city from: {title}")
            results[ticker] = {"error": "could not extract city", "title": title}
            continue

        coords = geocode_city(city)
        if not coords:
            logger.warning(f"    Could not geocode city: {city}")
            results[ticker] = {"error": f"geocoding failed for {city}", "title": title}
            continue

        lat, lon = coords
        forecast_date = extract_forecast_date(market)
        logger.info(f"    City: {city} ({lat:.2f}, {lon:.2f}), date: {forecast_date}")

        # Try NWS first (matches Kalshi settlement source)
        forecast = fetch_nws_forecast(lat, lon, forecast_date)
        if forecast:
            logger.info(f"    NWS: high={forecast.get('temp_high_f')}°F  low={forecast.get('temp_low_f')}°F")
        else:
            logger.warning(f"    NWS unavailable — trying Open-Meteo fallback")
            forecast = fetch_openmeteo_fallback(lat, lon, forecast_date)
            if forecast:
                logger.info(f"    Open-Meteo: high={forecast.get('temp_high_f')}°F  low={forecast.get('temp_low_f')}°F")

        if not forecast:
            logger.warning(f"    Both NWS and Open-Meteo failed")
            results[ticker] = {"error": "forecast unavailable", "city": city, "title": title}
            continue

        summary = build_forecast_summary(forecast, title)

        results[ticker] = {
            "title":             title,
            "city":              city,
            "lat":               lat,
            "lon":               lon,
            "forecast_date":     forecast_date,
            "forecast":          forecast,
            "forecast_summary":  summary,
            "is_weather_market": True,
        }

        time.sleep(0.3)

    return results


def main():
    if not SCAN_RESULTS.exists():
        raise FileNotFoundError(str(SCAN_RESULTS))

    markets = json.loads(SCAN_RESULTS.read_text())
    logger.info(f"Scanning {len(markets)} markets for weather bets...")

    results = scrape_weather(markets)

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(results, indent=2))
    logger.info(f"Saved weather data → {OUTPUT} ({len(results)} markets)")
    return results


if __name__ == "__main__":
    main()

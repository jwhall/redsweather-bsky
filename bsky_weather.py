#!/usr/bin/env python3
"""
bsky_weather.py — Posts a Cincinnati Reds game-day weather forecast to Bluesky.

Data sources:
  - MLB Stats API via the `MLB-StatsAPI` package
      https://github.com/toddrob99/MLB-StatsAPI
      https://statsapi.mlb.com/docs/
  - Open-Meteo forecast API (no key)
      https://open-meteo.com/en/docs
  - Bluesky / AT Protocol via the `atproto` SDK
      https://atproto.blue/
      https://github.com/MarshalX/atproto
      AT Proto post lexicon: https://docs.bsky.app/docs/api/app-bsky-feed-post
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import statsapi
from atproto import Client
from atproto_client.exceptions import AtProtocolError
from dotenv import load_dotenv

REDS_TEAM_ID = 113
GREAT_AMERICAN_BALLPARK_VENUE_ID = 2602

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
HTTP_TIMEOUT = 15
BLUESKY_POST_LIMIT = 300  # graphemes; we stay well under so byte/grapheme counting isn't worth it.

# WMO weather interpretation codes — https://open-meteo.com/en/docs (Weather variable docs)
WMO_CODES: dict[int, str] = {
    0: "Clear skies",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Light rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Light snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorms",
    96: "Thunderstorms with light hail",
    99: "Thunderstorms with heavy hail",
}

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bsky_weather")


@dataclass
class GameInfo:
    game_id: int
    is_home: bool
    opponent: str
    venue_name: str
    venue_city: str
    latitude: float
    longitude: float
    venue_tz: str
    first_pitch_utc: datetime


@dataclass
class WeatherInfo:
    temperature_f: float
    description: str
    local_time: datetime


class NoGameToday(Exception):
    pass


def get_game_info(team_id: int = REDS_TEAM_ID, on_date: Optional[datetime] = None) -> GameInfo:
    """Find today's Reds game and return venue + first-pitch info.

    statsapi.schedule wiki:
      https://github.com/toddrob99/MLB-StatsAPI/wiki/Function:-schedule
    """
    on_date = on_date or datetime.now(tz=timezone.utc)
    date_str = on_date.strftime("%m/%d/%Y")
    log.info("Checking schedule for team %s on %s", team_id, date_str)

    games = statsapi.schedule(date=date_str, team=team_id)
    if not games:
        raise NoGameToday(f"No games scheduled for team {team_id} on {date_str}")

    games_sorted = sorted(games, key=lambda g: g.get("game_datetime", ""))
    game = next(
        (g for g in games_sorted if g.get("status", "").lower() not in {"postponed", "cancelled", "canceled"}),
        None,
    )
    if game is None:
        raise NoGameToday(f"All games on {date_str} are postponed/cancelled")

    is_home = int(game["home_id"]) == int(team_id)
    opponent = game["away_name"] if is_home else game["home_name"]
    venue_id = game.get("venue_id")
    first_pitch_utc = datetime.fromisoformat(game["game_datetime"].replace("Z", "+00:00"))

    venue = _get_venue_details(venue_id) if venue_id else {}
    coords = venue.get("location", {}).get("defaultCoordinates", {})
    lat = coords.get("latitude")
    lon = coords.get("longitude")
    tz_id = venue.get("timeZone", {}).get("id")
    city = venue.get("location", {}).get("city", "")

    if lat is None or lon is None:
        raise RuntimeError(
            f"Venue {venue_id} ({game.get('venue_name')}) is missing coordinates from MLB Stats API"
        )
    if not tz_id:
        log.warning("Venue %s has no timezone; defaulting to America/New_York", venue_id)
        tz_id = "America/New_York"

    return GameInfo(
        game_id=int(game["game_id"]),
        is_home=is_home,
        opponent=str(opponent),
        venue_name=str(game.get("venue_name", venue.get("name", "the ballpark"))),
        venue_city=str(city),
        latitude=float(lat),
        longitude=float(lon),
        venue_tz=str(tz_id),
        first_pitch_utc=first_pitch_utc,
    )


def _get_venue_details(venue_id: int) -> dict:
    """Hydrate a venue with location + timezone via the Stats API."""
    try:
        payload = statsapi.get("venue", {"venueIds": venue_id, "hydrate": "location"})
    except Exception as exc:
        log.warning("Failed to hydrate venue %s: %s", venue_id, exc)
        return {}
    venues = payload.get("venues", [])
    return venues[0] if venues else {}


def get_weather(game: GameInfo) -> WeatherInfo:
    """Fetch hourly forecast from Open-Meteo and pick the hour matching first pitch."""
    local_first_pitch = game.first_pitch_utc.astimezone(ZoneInfo(game.venue_tz))
    local_date = local_first_pitch.date().isoformat()

    params = {
        "latitude": game.latitude,
        "longitude": game.longitude,
        "hourly": "temperature_2m,weather_code",
        "temperature_unit": "fahrenheit",
        "timezone": game.venue_tz,
        "start_date": local_date,
        "end_date": local_date,
    }
    log.info(
        "Fetching weather: lat=%s lon=%s tz=%s date=%s",
        game.latitude, game.longitude, game.venue_tz, local_date,
    )

    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Open-Meteo request failed: {exc}") from exc

    data = resp.json()
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    codes = hourly.get("weather_code", [])
    if not times:
        raise RuntimeError("Open-Meteo returned no hourly data")

    target_hour = local_first_pitch.replace(minute=0, second=0, microsecond=0)
    if local_first_pitch.minute >= 30 and target_hour.hour < 23:
        target_hour = target_hour.replace(hour=target_hour.hour + 1)
    target_iso = target_hour.strftime("%Y-%m-%dT%H:%M")

    try:
        idx = times.index(target_iso)
    except ValueError:
        log.warning("Exact hour %s not in forecast; using closest", target_iso)
        idx = min(
            range(len(times)),
            key=lambda i: abs(datetime.fromisoformat(times[i]) - target_hour.replace(tzinfo=None)),
        )

    code = int(codes[idx])
    description = WMO_CODES.get(code, f"Weather code {code}")
    return WeatherInfo(
        temperature_f=float(temps[idx]),
        description=description,
        local_time=local_first_pitch,
    )


def format_post(game: GameInfo, weather: WeatherInfo) -> str:
    matchup_verb = "vs" if game.is_home else "at"
    venue_clause = (
        f"at {game.venue_name}"
        if game.is_home
        else f"at {game.venue_name} in {game.venue_city}" if game.venue_city else f"at {game.venue_name}"
    )
    pitch_local = weather.local_time.strftime("%-I:%M %p %Z")
    text = (
        f"Reds {matchup_verb} {game.opponent}. "
        f"First pitch {pitch_local} {venue_clause}: "
        f"{round(weather.temperature_f)}°F, {weather.description}."
    )
    if len(text) > BLUESKY_POST_LIMIT:
        text = text[: BLUESKY_POST_LIMIT - 1] + "…"
    return text


def post_to_bluesky(message: str, handle: str, app_password: str, pds_host: str = "https://bsky.social") -> str:
    """Authenticate and publish a single text post.

    The atproto SDK wraps:
      - com.atproto.server.createSession (login)
      - com.atproto.repo.createRecord    (send_post)
    See https://docs.bsky.app/docs/get-started and
        https://docs.bsky.app/docs/advanced-guides/posts
    """
    client = Client(base_url=pds_host)
    try:
        client.login(handle, app_password)
    except AtProtocolError as exc:
        raise RuntimeError(f"Bluesky login failed for {handle}: {exc}") from exc

    try:
        result = client.send_post(text=message)
    except AtProtocolError as exc:
        # Rate limits surface as AtProtocolError with HTTP 429 in the response payload.
        raise RuntimeError(f"Bluesky post failed: {exc}") from exc

    return getattr(result, "uri", "")


def main() -> int:
    load_dotenv()
    handle = os.environ.get("BLUESKY_HANDLE")
    app_password = os.environ.get("BLUESKY_APP_PASSWORD")
    pds_host = os.environ.get("PDS_HOST", "https://bsky.social")
    dry_run = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}

    if not dry_run and (not handle or not app_password):
        log.error("Missing BLUESKY_HANDLE or BLUESKY_APP_PASSWORD in environment")
        return 2

    try:
        game = get_game_info()
    except NoGameToday as exc:
        log.info("%s", exc)
        return 0
    except Exception as exc:
        log.exception("Failed to retrieve game info: %s", exc)
        return 1

    try:
        weather = get_weather(game)
    except Exception as exc:
        log.exception("Failed to retrieve weather: %s", exc)
        return 1

    message = format_post(game, weather)
    log.info("Post text (%d chars): %s", len(message), message)

    if dry_run:
        print(message)
        return 0

    try:
        uri = post_to_bluesky(message, handle, app_password, pds_host)  # type: ignore[arg-type]
    except Exception as exc:
        log.exception("Failed to post to Bluesky: %s", exc)
        return 1

    log.info("Posted to Bluesky (uri=%s)", uri)
    return 0


if __name__ == "__main__":
    sys.exit(main())

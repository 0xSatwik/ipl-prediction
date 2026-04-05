from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import kagglehub
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
GENERATED_DIR = DATA_DIR / "generated"
LOCAL_DIR = DATA_DIR / "local"
OFFICIAL_MATCH_DIR = RAW_DIR / "official_2026_matches"
SCHEDULE_CACHE = RAW_DIR / "official_schedule_2026.json"
SQUAD_CACHE = RAW_DIR / "current_squads_2026.json"
PROFILE_CACHE = RAW_DIR / "player_profiles_2026.json"
APP_DATA_PATH = GENERATED_DIR / "app-data.json"
DUCKDB_PATH = LOCAL_DIR / "ipl.duckdb"

KAGGLE_DATASET = "chaitu20/ipl-dataset2008-2025"
KAGGLE_FILE = "IPL.csv"
YEAR_CUTOFF = 2025
CURRENT_SEASON = 2026
OFFICIAL_COMPETITION_ID = 284
OFFICIAL_FEED_ROOT = "https://scores.iplt20.com/ipl/feeds"
OFFICIAL_SCHEDULE_URL = f"{OFFICIAL_FEED_ROOT}/{OFFICIAL_COMPETITION_ID}-matchschedule.js"

ELO_K = 32
ELO_BASE = 1500
BAYESIAN_PRIOR_STRENGTH = 8
EWMA_ALPHA = 0.82
EWMA_INNINGS_WINDOW = 12

NON_BOWLER_WICKETS = {
    "run out",
    "retired hurt",
    "retired out",
    "obstructing the field",
    "handled the ball",
}

TEAM_META = [
    {
        "id": "csk",
        "name": "Chennai Super Kings",
        "shortName": "CSK",
        "slug": "chennai-super-kings",
        "colors": ["#f3cb46", "#103f88"],
    },
    {
        "id": "dc",
        "name": "Delhi Capitals",
        "shortName": "DC",
        "slug": "delhi-capitals",
        "colors": ["#0f5fc1", "#d71920"],
    },
    {
        "id": "gt",
        "name": "Gujarat Titans",
        "shortName": "GT",
        "slug": "gujarat-titans",
        "colors": ["#10233f", "#96c5ff"],
    },
    {
        "id": "kkr",
        "name": "Kolkata Knight Riders",
        "shortName": "KKR",
        "slug": "kolkata-knight-riders",
        "colors": ["#44216b", "#d9b44a"],
    },
    {
        "id": "lsg",
        "name": "Lucknow Super Giants",
        "shortName": "LSG",
        "slug": "lucknow-super-giants",
        "colors": ["#00a7e1", "#ef7f1a"],
    },
    {
        "id": "mi",
        "name": "Mumbai Indians",
        "shortName": "MI",
        "slug": "mumbai-indians",
        "colors": ["#005da0", "#c6a560"],
    },
    {
        "id": "pbks",
        "name": "Punjab Kings",
        "shortName": "PBKS",
        "slug": "punjab-kings",
        "colors": ["#c10f2d", "#d6b77a"],
    },
    {
        "id": "rr",
        "name": "Rajasthan Royals",
        "shortName": "RR",
        "slug": "rajasthan-royals",
        "colors": ["#ec1c87", "#1d2d5c"],
    },
    {
        "id": "rcb",
        "name": "Royal Challengers Bengaluru",
        "shortName": "RCB",
        "slug": "royal-challengers-bengaluru",
        "colors": ["#d71920", "#1a1a1a"],
    },
    {
        "id": "srh",
        "name": "Sunrisers Hyderabad",
        "shortName": "SRH",
        "slug": "sunrisers-hyderabad",
        "colors": ["#f26522", "#111111"],
    },
]

TEAM_NAME_MAP = {
    "Royal Challengers Bangalore": "Royal Challengers Bengaluru",
    "Delhi Daredevils": "Delhi Capitals",
    "Kings XI Punjab": "Punjab Kings",
}


def normalize_name(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


AVAILABILITY_OVERRIDES = {
    ("Chennai Super Kings", normalize_name("Spencer Johnson")): 0.92,
    ("Chennai Super Kings", normalize_name("Nathan Ellis")): 0.18,
    ("Kolkata Knight Riders", normalize_name("Navdeep Saini")): 0.84,
    ("Kolkata Knight Riders", normalize_name("Harshit Rana")): 0.26,
    ("Gujarat Titans", normalize_name("Kulwant Khejroliya")): 0.78,
    ("Gujarat Titans", normalize_name("Prithviraj Yarra")): 0.15,
    ("Sunrisers Hyderabad", normalize_name("Pat Cummins")): 0.68,
}


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def canonical_team(value: str | None) -> str:
    if not value:
        return "Unknown"
    return TEAM_NAME_MAP.get(value, value)


def safe_float(value: Any, digits: int = 2) -> float:
    if value is None:
        return 0.0
    if isinstance(value, float) and math.isnan(value):
        return 0.0
    return round(float(value), digits)


def safe_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, float) and math.isnan(value):
        return 0
    if value == "":
        return 0
    return int(float(value))


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def ensure_dirs() -> None:
    for directory in (RAW_DIR, GENERATED_DIR, LOCAL_DIR, OFFICIAL_MATCH_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def dataset_path() -> Path:
    cache_dir = Path(kagglehub.dataset_download(KAGGLE_DATASET))
    csv_path = cache_dir / KAGGLE_FILE
    if not csv_path.exists():
        raise FileNotFoundError(f"Expected Kaggle file at {csv_path}")
    return csv_path


def requests_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }
    )
    return session


def normalize_role(label: str) -> str:
    cleaned = label.strip().replace("  ", " ")
    lowered = cleaned.casefold()
    if "keeper" in lowered or lowered.startswith("wk") or "wk-" in lowered:
        return "Wicketkeeper"
    if "all" in lowered:
        return "All-Rounder"
    if "bowl" in lowered:
        return "Bowler"
    return "Batter"


def normalize_official_role(skill: str, is_wk: Any) -> str:
    if str(is_wk) == "1":
        return "Wicketkeeper"
    return normalize_role(skill)


def clean_feed_player_name(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value
    for _ in range(4):
        cleaned = re.sub(r"\s*\((?:c|vc|wk|ip|rp)\)\s*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,-")
    return cleaned


def parse_jsonp(text: str) -> Any:
    stripped = text.strip().rstrip(";")
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped)
    open_index = stripped.find("(")
    close_index = stripped.rfind(")")
    if open_index == -1 or close_index == -1 or close_index <= open_index:
        raise ValueError("Unable to parse JSONP payload")
    return json.loads(stripped[open_index + 1 : close_index])


def fetch_jsonp(session: requests.Session, url: str, cache_path: Path | None = None) -> Any:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    payload = parse_jsonp(response.text)
    if cache_path is not None:
        write_json(cache_path, payload)
    return payload


def parse_match_datetime(row: dict[str, Any]) -> datetime | None:
    for value in (row.get("MatchDateTime"), row.get("MATCH_COMMENCE_START_DATE"), row.get("MatchDate")):
        if not value:
            continue
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.notna(parsed):
            return parsed.to_pydatetime()
    return None


def parse_toss_details(value: str | None) -> tuple[str, str]:
    if not value:
        return "", ""
    winner = value.split(" Won", 1)[0].strip()
    lowered = value.casefold()
    if "field" in lowered or "bowl" in lowered:
        return canonical_team(winner), "field"
    if "bat" in lowered:
        return canonical_team(winner), "bat"
    return canonical_team(winner), ""


def parse_runs(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return safe_int(value)
    match = re.search(r"-?\d+", str(value))
    return int(match.group()) if match else 0


def parse_ball_token(value: Any) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def scrape_player_profile(session: requests.Session, url: str, cache: dict[str, Any]) -> dict[str, Any]:
    if url in cache:
        return cache[url]

    response = session.get(url.strip(), timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    text = " ".join(soup.stripped_strings)

    nationality = re.search(r"Nationality\s+([A-Za-z ]+?)\s+Bats", text)
    bats = re.search(r"Bats\s+([A-Za-z ]+?)\s+Bowls", text)
    bowls = re.search(r"Bowls\s+([A-Za-z .\-]+?)\s+Bio", text)
    role = re.search(r"Role\s+([A-Za-z\- ]+?)\s+Nationality", text)

    payload = {
        "nationality": nationality.group(1).strip() if nationality else "Unknown",
        "bats": bats.group(1).strip() if bats else "",
        "bowls": bowls.group(1).strip() if bowls else "",
        "roleDetail": role.group(1).strip() if role else "",
    }
    cache[url] = payload
    return payload


def scrape_current_squads(refresh: bool = True) -> list[dict[str, Any]]:
    if not refresh and SQUAD_CACHE.exists():
        cached = load_json(SQUAD_CACHE, [])
        if cached:
            return cached

    session = requests_session()
    profile_cache: dict[str, Any] = load_json(PROFILE_CACHE, {})
    squads: list[dict[str, Any]] = []

    for team in TEAM_META:
        url = f"https://www.iplt20.com/teams/{team['slug']}/squad/2026"
        response = session.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select("div.ih-pcard-wrap ul li a[data-player_name]")

        seen: set[str] = set()
        for card in cards:
            name = clean_feed_player_name(card.get("data-player_name", "").strip())
            if not name:
                continue

            player_key = normalize_name(name)
            if player_key in seen:
                continue
            seen.add(player_key)

            role_label = ""
            role_node = card.select_one("span.d-block.w-100.text-center")
            if role_node:
                role_label = role_node.get_text(strip=True)

            image_node = card.select_one("img.lazyload")
            profile_url = card.get("href", "").strip()
            profile = scrape_player_profile(session, profile_url, profile_cache) if profile_url else {}
            nationality = profile.get("nationality", "Unknown")

            squads.append(
                {
                    "team": team["name"],
                    "teamId": team["id"],
                    "teamSlug": team["slug"],
                    "teamShortName": team["shortName"],
                    "teamColors": team["colors"],
                    "name": name,
                    "playerKey": player_key,
                    "slug": slugify(name),
                    "role": normalize_role(role_label or profile.get("roleDetail", "")),
                    "roleLabel": role_label or profile.get("roleDetail", ""),
                    "isCaptain": bool(card.select_one("img[src*='captain-icon']")),
                    "isOverseas": nationality != "Indian",
                    "nationality": nationality,
                    "bats": profile.get("bats", ""),
                    "bowls": profile.get("bowls", ""),
                    "profileUrl": profile_url,
                    "imageUrl": image_node.get("data-src", "") if image_node else "",
                }
            )

    write_json(SQUAD_CACHE, squads)
    write_json(PROFILE_CACHE, profile_cache)
    return squads


def current_squads() -> list[dict[str, Any]]:
    try:
        return scrape_current_squads(refresh=True)
    except requests.RequestException:
        cached = load_json(SQUAD_CACHE, [])
        if cached:
            return cached
        raise


def build_fixtures(schedule_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fixtures = []
    now = datetime.now()
    for row in schedule_rows:
        match_time = parse_match_datetime(row)
        if match_time is None or match_time < now:
            continue
        fixtures.append(
            {
                "matchId": safe_int(row.get("MatchID")),
                "date": match_time.isoformat(),
                "label": row.get("MatchDateNew") or match_time.strftime("%d %b %Y"),
                "teamA": canonical_team(row.get("HomeTeamName") or row.get("FirstBattingTeamName")),
                "teamB": canonical_team(row.get("AwayTeamName") or row.get("SecondBattingTeamName")),
                "venue": row.get("GroundName", "Unknown").strip(),
                "city": row.get("city", "Unknown").strip() or "Unknown",
                "status": row.get("MatchStatus", "Upcoming"),
            }
        )
    fixtures.sort(key=lambda item: item["date"])
    return fixtures[:8]


def merge_official_squad_meta(
    squad_payload: dict[str, Any],
    official_meta: dict[tuple[str, str], dict[str, Any]],
    id_map: dict[str, str],
) -> None:
    for key in ("squadA", "squadB"):
        for row in squad_payload.get(key, []):
            team = canonical_team(row.get("TeamName"))
            name = clean_feed_player_name(row.get("PlayerName"))
            player_key = normalize_name(name)
            if not player_key:
                continue
            role_label = row.get("PlayerSkill", "")
            official_meta[(team, player_key)] = {
                "name": name,
                "role": normalize_official_role(role_label, row.get("IsWK")),
                "roleLabel": role_label or ("Wicketkeeper" if str(row.get("IsWK")) == "1" else ""),
                "isOverseas": str(row.get("IsNonDomestic")) == "1",
                "bats": row.get("BattingType", ""),
                "bowls": row.get("BowlingProficiency", ""),
                "imageUrl": row.get("PlayerImage", ""),
            }
            player_id = str(row.get("PlayerID", "")).strip()
            if player_id:
                id_map[player_id] = name


def update_player_usage(
    usage: dict[tuple[str, str], dict[str, Any]],
    team_name: str,
    batting_card: list[dict[str, Any]],
    match_id: int,
    match_time: datetime,
) -> None:
    for row in batting_card:
        raw_name = row.get("PlayerName", "")
        clean_name = clean_feed_player_name(raw_name)
        player_key = normalize_name(clean_name)
        if not player_key:
            continue

        item = usage.setdefault(
            (team_name, player_key),
            {
                "name": clean_name,
                "matchIds": set(),
                "startIds": set(),
                "impactIds": set(),
                "startDates": [],
                "lastSeen": None,
            },
        )
        item["matchIds"].add(match_id)
        if "(IP)" in raw_name.upper():
            item["impactIds"].add(match_id)
        else:
            item["startIds"].add(match_id)
            item["startDates"].append(match_time)

        if item["lastSeen"] is None or match_time > item["lastSeen"]:
            item["lastSeen"] = match_time


def finalize_usage(
    usage: dict[tuple[str, str], dict[str, Any]]
) -> dict[tuple[str, str], dict[str, Any]]:
    if not usage:
        return {}

    latest_seen = max((item["lastSeen"] for item in usage.values() if item["lastSeen"] is not None), default=None)
    recent_cutoff = latest_seen - timedelta(days=14) if latest_seen else None
    finalized: dict[tuple[str, str], dict[str, Any]] = {}

    for key, item in usage.items():
        recent_starts = 0
        if recent_cutoff is not None:
            recent_starts = sum(1 for date in item["startDates"] if date >= recent_cutoff)

        if latest_seen and item["lastSeen"] is not None:
            days_since = max((latest_seen - item["lastSeen"]).days, 0)
            recency_score = max(0.28, 1 - min(days_since, 35) / 42)
        else:
            recency_score = 0.45

        finalized[key] = {
            "name": item["name"],
            "matches2026": safe_int(len(item["matchIds"])),
            "starts2026": safe_int(len(item["startIds"])),
            "impactMatches2026": safe_int(len(item["impactIds"])),
            "recentStarts2026": safe_int(recent_starts),
            "lastSeen": item["lastSeen"].date().isoformat() if item["lastSeen"] else "",
            "recencyScore": safe_float(recency_score),
        }

    return finalized


def build_match_id_name_map(
    squad_payload: dict[str, Any], innings_payloads: dict[int, dict[str, Any]]
) -> dict[str, str]:
    id_map: dict[str, str] = {}
    merge_official_squad_meta(squad_payload, {}, id_map)

    for innings in innings_payloads.values():
        for row in innings.get("BattingCard", []):
            player_id = str(row.get("PlayerID", "")).strip()
            if player_id:
                id_map[player_id] = clean_feed_player_name(row.get("PlayerName"))
        for row in innings.get("BowlingCard", []):
            player_id = str(row.get("PlayerID", "")).strip()
            if player_id:
                id_map[player_id] = clean_feed_player_name(row.get("PlayerName"))

    return id_map


def summary_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return payload.get("MatchSummary") or payload.get("Matchsummary") or []


def convert_official_match(
    schedule_row: dict[str, Any],
    summary_row: dict[str, Any],
    squad_payload: dict[str, Any],
    innings_payloads: dict[int, dict[str, Any]],
    usage: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    match_id = safe_int(schedule_row.get("MatchID") or summary_row.get("MatchID"))
    match_time = parse_match_datetime(summary_row) or parse_match_datetime(schedule_row) or datetime(CURRENT_SEASON, 1, 1)
    toss_winner, toss_decision = parse_toss_details(summary_row.get("TossDetails") or schedule_row.get("TossDetails"))
    venue = (schedule_row.get("GroundName") or str(summary_row.get("GroundName", "")).split(",")[0]).strip() or "Unknown"
    city = (schedule_row.get("city") or "").strip() or "Unknown"
    player_of_match = clean_feed_player_name(str(summary_row.get("MOM", "")).split("(")[0].strip())
    win_outcome = summary_row.get("Comments") or schedule_row.get("Comments") or schedule_row.get("Commentss") or ""

    team_by_id = {
        str(schedule_row.get("FirstBattingTeamID", "")): canonical_team(schedule_row.get("FirstBattingTeamName")),
        str(schedule_row.get("SecondBattingTeamID", "")): canonical_team(schedule_row.get("SecondBattingTeamName")),
        str(schedule_row.get("HomeTeamID", "")): canonical_team(schedule_row.get("HomeTeamName")),
        str(schedule_row.get("AwayTeamID", "")): canonical_team(schedule_row.get("AwayTeamName")),
        str(summary_row.get("FirstBattingTeamID", "")): canonical_team(summary_row.get("FirstBattingTeam")),
        str(summary_row.get("SecondBattingTeamID", "")): canonical_team(summary_row.get("SecondBattingTeam")),
        str(summary_row.get("HomeTeamID", "")): canonical_team(summary_row.get("HomeTeamName")),
        str(summary_row.get("AwayTeamID", "")): canonical_team(summary_row.get("AwayTeamName")),
    }

    batting_first = canonical_team(summary_row.get("FirstBattingTeam") or schedule_row.get("FirstBattingTeamName"))
    chasing_team = canonical_team(summary_row.get("SecondBattingTeam") or schedule_row.get("SecondBattingTeamName"))
    winner = team_by_id.get(str(summary_row.get("WinningTeamID", "")).strip(), "")
    player_id_map = build_match_id_name_map(squad_payload, innings_payloads)

    if batting_first != "Unknown" and 1 in innings_payloads:
        update_player_usage(usage, batting_first, innings_payloads[1].get("BattingCard", []), match_id, match_time)
    if chasing_team != "Unknown" and 2 in innings_payloads:
        update_player_usage(usage, chasing_team, innings_payloads[2].get("BattingCard", []), match_id, match_time)

    rows: list[dict[str, Any]] = []
    for innings_no, innings in sorted(innings_payloads.items()):
        batting_team = batting_first if innings_no == 1 else chasing_team
        bowling_team = chasing_team if innings_no == 1 else batting_first

        team_runs = 0
        team_balls = 0
        team_wickets = 0

        for event in innings.get("OverHistory", []):
            total_runs = parse_runs(event.get("RunRuns") or event.get("Runs"))
            batter_runs = parse_runs(event.get("ActualRuns"))
            is_wide = str(event.get("IsWide")) == "1"
            is_no_ball = str(event.get("IsNoBall")) == "1"
            is_bye = str(event.get("IsBye")) == "1"
            is_leg_bye = str(event.get("IsLegBye")) == "1"
            valid_ball = 0 if (is_wide or is_no_ball) else 1
            runs_bowler = total_runs if not (is_bye or is_leg_bye) else (1 if is_no_ball else 0)
            is_wicket = str(event.get("IsWicket")) == "1"

            team_runs += total_runs
            team_balls += valid_ball
            team_wickets += 1 if is_wicket else 0

            out_id = str(event.get("OutBatsManID", "")).strip()
            rows.append(
                {
                    "match_id": match_id,
                    "date": match_time.date().isoformat(),
                    "innings": innings_no,
                    "batting_team": batting_team,
                    "bowling_team": bowling_team,
                    "over": safe_int(event.get("OverNo")),
                    "ball_no": parse_ball_token(event.get("BallName")),
                    "batter": clean_feed_player_name(event.get("BatsManName")),
                    "runs_batter": batter_runs,
                    "bowler": clean_feed_player_name(event.get("BowlerName")),
                    "valid_ball": valid_ball,
                    "runs_total": total_runs,
                    "runs_bowler": runs_bowler,
                    "wicket_kind": str(event.get("WicketType", "")).strip().casefold() or None,
                    "player_out": player_id_map.get(out_id, ""),
                    "toss_winner": toss_winner,
                    "toss_decision": toss_decision,
                    "venue": venue,
                    "city": city,
                    "player_of_match": player_of_match,
                    "match_won_by": winner,
                    "win_outcome": win_outcome,
                    "season": CURRENT_SEASON,
                    "year": CURRENT_SEASON,
                    "team_runs": team_runs,
                    "team_balls": team_balls,
                    "team_wicket": team_wickets,
                }
            )

    return rows


def fetch_official_2026_data() -> tuple[pd.DataFrame, dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    session = requests_session()
    schedule_payload = fetch_jsonp(session, OFFICIAL_SCHEDULE_URL, SCHEDULE_CACHE)
    schedule_rows = schedule_payload.get("Matchsummary", [])
    fixtures = build_fixtures(schedule_rows)
    official_meta: dict[tuple[str, str], dict[str, Any]] = {}
    usage: dict[tuple[str, str], dict[str, Any]] = {}
    deliveries: list[dict[str, Any]] = []

    played_rows = [row for row in schedule_rows if str(row.get("MatchStatus", "")).casefold() == "post"]
    played_rows.sort(key=lambda row: parse_match_datetime(row) or datetime(CURRENT_SEASON, 1, 1))

    for row in played_rows:
        match_id = safe_int(row.get("MatchID"))
        summary_payload = fetch_jsonp(
            session,
            f"{OFFICIAL_FEED_ROOT}/{match_id}-matchsummary.js",
            OFFICIAL_MATCH_DIR / f"{match_id}-matchsummary.json",
        )
        summary_list = summary_rows(summary_payload)
        if not summary_list:
            continue
        summary_row = summary_list[0]

        squad_payload = fetch_jsonp(
            session,
            f"{OFFICIAL_FEED_ROOT}/squads/{match_id}-squad.js",
            OFFICIAL_MATCH_DIR / f"{match_id}-squad.json",
        )
        id_map: dict[str, str] = {}
        merge_official_squad_meta(squad_payload, official_meta, id_map)

        innings_payloads: dict[int, dict[str, Any]] = {}
        for innings_no in (1, 2):
            try:
                innings_payload = fetch_jsonp(
                    session,
                    f"{OFFICIAL_FEED_ROOT}/{match_id}-Innings{innings_no}.js",
                    OFFICIAL_MATCH_DIR / f"{match_id}-Innings{innings_no}.json",
                )
            except requests.HTTPError:
                continue

            innings = innings_payload.get(f"Innings{innings_no}")
            if innings:
                innings_payloads[innings_no] = innings

        if 1 not in innings_payloads or 2 not in innings_payloads:
            continue

        deliveries.extend(convert_official_match(row, summary_row, squad_payload, innings_payloads, usage))

    return pd.DataFrame(deliveries), official_meta, finalize_usage(usage), fixtures


def prepare_history_frame(frame: pd.DataFrame) -> pd.DataFrame:
    frame = pick_columns(frame)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["venue"] = frame["venue"].fillna("Unknown").astype(str).str.strip()
    frame["city"] = frame["city"].fillna("Unknown").astype(str).str.strip()

    numeric_columns = [
        "match_id",
        "innings",
        "over",
        "ball_no",
        "runs_batter",
        "valid_ball",
        "runs_total",
        "runs_bowler",
        "season",
        "year",
        "team_runs",
        "team_balls",
        "team_wicket",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)

    frame["batting_team"] = frame["batting_team"].map(canonical_team)
    frame["bowling_team"] = frame["bowling_team"].map(canonical_team)
    frame["toss_winner"] = frame["toss_winner"].map(canonical_team)
    frame["match_won_by"] = frame["match_won_by"].map(canonical_team)
    frame["batter"] = frame["batter"].fillna("").map(clean_feed_player_name)
    frame["bowler"] = frame["bowler"].fillna("").map(clean_feed_player_name)
    frame["player_out"] = frame["player_out"].fillna("").map(clean_feed_player_name)
    frame["batter_key"] = frame["batter"].map(normalize_name)
    frame["bowler_key"] = frame["bowler"].map(normalize_name)
    frame["player_out_key"] = frame["player_out"].map(normalize_name)
    frame["wicket_kind"] = frame["wicket_kind"].where(frame["wicket_kind"].notna(), None)
    frame["is_bowler_wicket"] = frame["wicket_kind"].notna() & ~frame["wicket_kind"].isin(list(NON_BOWLER_WICKETS))
    frame["is_batter_out"] = frame["player_out_key"] == frame["batter_key"]
    frame["is_four"] = frame["runs_batter"] == 4
    frame["is_six"] = frame["runs_batter"] == 6
    frame["is_boundary"] = frame["is_four"] | frame["is_six"]
    frame["scenario"] = frame["innings"].map(lambda value: "bat_first" if safe_int(value) == 1 else "chasing")
    # Phase decomposition: powerplay (0-5), middle (6-15), death (16-19)
    frame["phase"] = frame["over"].map(lambda o: "powerplay" if int(o) < 6 else ("death" if int(o) >= 16 else "middle"))
    return frame


def load_kaggle_history() -> pd.DataFrame:
    csv_path = dataset_path()
    frame = pd.read_csv(csv_path, low_memory=False)
    history = frame[frame["year"] <= YEAR_CUTOFF].copy()
    return prepare_history_frame(history)


def pick_columns(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "match_id",
        "date",
        "innings",
        "batting_team",
        "bowling_team",
        "over",
        "ball_no",
        "batter",
        "runs_batter",
        "bowler",
        "valid_ball",
        "runs_total",
        "runs_bowler",
        "wicket_kind",
        "player_out",
        "toss_winner",
        "toss_decision",
        "venue",
        "city",
        "player_of_match",
        "match_won_by",
        "win_outcome",
        "season",
        "year",
        "team_runs",
        "team_balls",
        "team_wicket",
    ]
    return frame[columns].copy()


def merge_current_squad_context(
    scraped_squads: list[dict[str, Any]],
    official_meta: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    team_meta_by_name = {team["name"]: team for team in TEAM_META}

    for player in scraped_squads:
        key = (player["team"], player["playerKey"])
        seen.add(key)
        current = {**player}
        official = official_meta.get(key)
        if official:
            current["name"] = official.get("name", current["name"])
            current["slug"] = slugify(current["name"])
            current["role"] = official.get("role", current["role"])
            current["roleLabel"] = official.get("roleLabel", current["roleLabel"])
            current["isOverseas"] = official.get("isOverseas", current["isOverseas"])
            current["nationality"] = "Overseas" if current["isOverseas"] else current["nationality"]
            current["bats"] = official.get("bats", current["bats"])
            current["bowls"] = official.get("bowls", current["bowls"])
            if official.get("imageUrl"):
                current["imageUrl"] = official["imageUrl"]
        merged.append(current)

    for key, official in official_meta.items():
        if key in seen:
            continue
        team_name, player_key = key
        team = team_meta_by_name.get(team_name)
        if not team:
            continue
        merged.append(
            {
                "team": team_name,
                "teamId": team["id"],
                "teamSlug": team["slug"],
                "teamShortName": team["shortName"],
                "teamColors": team["colors"],
                "name": official["name"],
                "playerKey": player_key,
                "slug": slugify(official["name"]),
                "role": official.get("role", "Batter"),
                "roleLabel": official.get("roleLabel", ""),
                "isCaptain": False,
                "isOverseas": official.get("isOverseas", False),
                "nationality": "Overseas" if official.get("isOverseas") else "Indian",
                "bats": official.get("bats", ""),
                "bowls": official.get("bowls", ""),
                "profileUrl": "",
                "imageUrl": official.get("imageUrl", ""),
            }
        )

    merged.sort(key=lambda item: (item["team"], item["name"]))
    return merged


# ---------------------------------------------------------------------------
#  Elo rating system
# ---------------------------------------------------------------------------

def compute_elo_ratings(match_meta: pd.DataFrame) -> dict[str, float]:
    """Process all matches chronologically and compute Elo for each current franchise."""
    current_franchises = {team["name"] for team in TEAM_META}
    elo: dict[str, float] = {team["name"]: ELO_BASE for team in TEAM_META}

    sorted_matches = match_meta.sort_values("date").to_dict("records")
    for row in sorted_matches:
        team_a = str(row.get("battingFirstTeam", ""))
        team_b = str(row.get("chasingTeam", ""))
        winner = str(row.get("match_won_by", ""))
        if team_a not in current_franchises or team_b not in current_franchises:
            continue
        if winner not in (team_a, team_b):
            continue

        elo_a = elo.get(team_a, ELO_BASE)
        elo_b = elo.get(team_b, ELO_BASE)
        expected_a = 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))
        actual_a = 1.0 if winner == team_a else 0.0

        # Apply season decay: recent seasons matter more
        season = safe_int(row.get("season", 0))
        season_recency = max(0.4, 1.0 - (CURRENT_SEASON - season) * 0.04)
        k = ELO_K * season_recency

        elo[team_a] = elo_a + k * (actual_a - expected_a)
        elo[team_b] = elo_b + k * ((1 - actual_a) - (1 - expected_a))

    return {team: safe_float(rating) for team, rating in elo.items() if team in current_franchises}


# ---------------------------------------------------------------------------
#  EWMA form score
# ---------------------------------------------------------------------------

def compute_ewma_form(history: pd.DataFrame, current_keys: set[str]) -> dict[str, float]:
    """Compute exponentially weighted moving average form from recent innings."""
    # Build per-innings fantasy-like scores for batters
    bat_innings = (
        history[history["batter_key"].isin(current_keys)]
        .groupby(["match_id", "batter_key", "date"], as_index=False)
        .agg(
            runs=("runs_batter", "sum"),
            balls=("valid_ball", "sum"),
            fours=("is_four", "sum"),
            sixes=("is_six", "sum"),
        )
    )
    bat_innings["bat_score"] = (
        bat_innings["runs"] * 1.0
        + bat_innings["fours"] * 0.5
        + bat_innings["sixes"] * 1.0
        + (bat_innings["runs"] >= 50).astype(float) * 8
        + (bat_innings["runs"] >= 100).astype(float) * 16
        + np.where(bat_innings["balls"] > 0, bat_innings["runs"] / bat_innings["balls"].clip(lower=1) * 10, 0)
    )

    # Build per-innings fantasy-like scores for bowlers
    bowl_innings = (
        history[history["bowler_key"].isin(current_keys)]
        .groupby(["match_id", "bowler_key", "date"], as_index=False)
        .agg(
            wickets=("is_bowler_wicket", "sum"),
            dots=("runs_total", lambda s: safe_int((s == 0).sum())),
            runs=("runs_bowler", "sum"),
            balls=("valid_ball", "sum"),
        )
    )
    bowl_innings["bowl_score"] = (
        bowl_innings["wickets"] * 25
        + bowl_innings["dots"] * 1.0
        + (bowl_innings["wickets"] >= 3).astype(float) * 8
        + (bowl_innings["wickets"] >= 5).astype(float) * 16
        - bowl_innings["runs"] * 0.5
    )

    form: dict[str, float] = {}

    for key in current_keys:
        bat_rows = bat_innings[bat_innings["batter_key"] == key].sort_values("date", ascending=False).head(EWMA_INNINGS_WINDOW)
        bowl_rows = bowl_innings[bowl_innings["bowler_key"] == key].sort_values("date", ascending=False).head(EWMA_INNINGS_WINDOW)

        scores: list[float] = []
        # Merge and sort by date descending
        all_dates: list[tuple[float, Any]] = []
        for _, r in bat_rows.iterrows():
            all_dates.append((float(r["bat_score"]), r["date"]))
        for _, r in bowl_rows.iterrows():
            all_dates.append((float(r["bowl_score"]), r["date"]))
        all_dates.sort(key=lambda x: x[1], reverse=True)
        scores = [s for s, _ in all_dates[:EWMA_INNINGS_WINDOW]]

        if not scores:
            form[key] = 0.5
            continue

        # EWMA: most recent innings weighted highest
        weights = [EWMA_ALPHA ** i for i in range(len(scores))]
        total_weight = sum(weights)
        weighted_sum = sum(s * w for s, w in zip(scores, weights))
        raw_form = weighted_sum / total_weight if total_weight > 0 else 0

        # Normalize to 0-1 scale: typical fantasy scores range 0-80
        form[key] = safe_float(min(1.0, max(0.0, raw_form / 60.0)), 3)

    return form


# ---------------------------------------------------------------------------
#  Expected batting position, balls faced, overs bowled
# ---------------------------------------------------------------------------

def compute_batting_position_stats(history: pd.DataFrame, current_keys: set[str]) -> dict[str, dict[str, float]]:
    """Estimate batting position, expected balls faced, expected overs bowled."""
    # For batting position: group by match+innings, rank batters by first ball faced
    batter_entries = history[history["batter_key"].isin(current_keys)].copy()
    batter_entries["delivery_index"] = batter_entries["over"] * 6 + batter_entries["ball_no"]

    first_ball = (
        batter_entries
        .groupby(["match_id", "innings", "batter_key"], as_index=False)
        .agg(first_delivery=("delivery_index", "min"), balls_faced=("valid_ball", "sum"))
    )

    # Compute batting position per innings (rank by first delivery within match+innings)
    first_ball["batting_position"] = first_ball.groupby(["match_id", "innings"])["first_delivery"].rank(method="min")

    position_stats: dict[str, dict[str, float]] = {}
    for key in current_keys:
        player_rows = first_ball[first_ball["batter_key"] == key]
        if player_rows.empty:
            position_stats[key] = {
                "expectedBattingPosition": 7.0,
                "expectedBallsFaced": 8.0,
                "expectedOversBowled": 0.0,
            }
            continue

        avg_pos = float(player_rows["batting_position"].mean())
        avg_balls = float(player_rows["balls_faced"].mean())

        position_stats[key] = {
            "expectedBattingPosition": safe_float(avg_pos),
            "expectedBallsFaced": safe_float(avg_balls),
            "expectedOversBowled": 0.0,  # filled below
        }

    # Overs bowled per match for bowlers
    bowler_entries = history[history["bowler_key"].isin(current_keys)].copy()
    bowler_per_match = (
        bowler_entries
        .groupby(["match_id", "bowler_key"], as_index=False)
        .agg(balls=("valid_ball", "sum"))
    )
    for key in current_keys:
        player_rows = bowler_per_match[bowler_per_match["bowler_key"] == key]
        if not player_rows.empty:
            avg_overs = float(player_rows["balls"].mean()) / 6.0
            if key in position_stats:
                position_stats[key]["expectedOversBowled"] = safe_float(avg_overs)
            else:
                position_stats[key] = {
                    "expectedBattingPosition": 9.0,
                    "expectedBallsFaced": 5.0,
                    "expectedOversBowled": safe_float(avg_overs),
                }

    return position_stats


# ---------------------------------------------------------------------------
#  Bayesian shrinkage helper
# ---------------------------------------------------------------------------

def bayesian_shrink(sample_value: float, sample_size: int, prior_value: float, prior_strength: int = BAYESIAN_PRIOR_STRENGTH) -> float:
    """Shrink a sample estimate toward a prior based on sample size."""
    return (sample_size * sample_value + prior_strength * prior_value) / (sample_size + prior_strength)


# ---------------------------------------------------------------------------
#  Global priors
# ---------------------------------------------------------------------------

def compute_global_priors(
    player_profiles: dict[str, dict[str, Any]],
    match_meta: pd.DataFrame,
    pair_stats_raw: pd.DataFrame,
) -> dict[str, Any]:
    """Compute global prior values for Bayesian shrinkage."""
    all_batting_indices = [p["batting"]["index"] for p in player_profiles.values() if p["batting"]["innings"] > 0]
    all_bowling_indices = [p["bowling"]["index"] for p in player_profiles.values() if p["bowling"]["innings"] > 0]
    all_first_innings = match_meta["firstInningsScore"].dropna()
    all_chase_wins = match_meta["winnerIsChasing"].dropna()

    return {
        "battingIndex": safe_float(np.mean(all_batting_indices) if all_batting_indices else 40.0),
        "bowlingIndex": safe_float(np.mean(all_bowling_indices) if all_bowling_indices else 18.0),
        "firstInningsAverage": safe_float(float(all_first_innings.mean()) if len(all_first_innings) > 0 else 165.0),
        "chaseWinRate": safe_float(float(all_chase_wins.mean()) * 100 if len(all_chase_wins) > 0 else 52.0),
        "pairEdge": 0.0,
        "priorStrength": BAYESIAN_PRIOR_STRENGTH,
    }


# ---------------------------------------------------------------------------
#  Decayed head-to-head
# ---------------------------------------------------------------------------

def build_decayed_head_to_head(match_meta: pd.DataFrame) -> list[dict[str, Any]]:
    """Build head-to-head records with exponential decay by season."""
    current_franchises = {team["name"] for team in TEAM_META}
    source = match_meta[
        match_meta["battingFirstTeam"].isin(current_franchises) & match_meta["chasingTeam"].isin(current_franchises)
    ].copy()
    source["pairKey"] = source.apply(
        lambda row: "|".join(sorted([row["battingFirstTeam"], row["chasingTeam"]])),
        axis=1,
    )

    rows = []
    for pair_key, group in source.groupby("pairKey"):
        team_a, team_b = pair_key.split("|")
        matches = safe_int(group["match_id"].nunique())

        # Raw counts
        wins_a = safe_int((group["match_won_by"] == team_a).sum())
        wins_b = safe_int((group["match_won_by"] == team_b).sum())

        # Recent counts (last 3 seasons)
        recent = group[group["season"] >= CURRENT_SEASON - 3]
        recent_matches = safe_int(recent["match_id"].nunique())
        recent_wins_a = safe_int((recent["match_won_by"] == team_a).sum())
        recent_wins_b = safe_int((recent["match_won_by"] == team_b).sum())

        # Decayed win rate: weight each match by 0.85^(years_ago)
        decayed_a = 0.0
        decayed_b = 0.0
        total_decay_weight = 0.0
        for _, row in group.iterrows():
            season = safe_int(row.get("season", CURRENT_SEASON))
            years_ago = max(CURRENT_SEASON - season, 0)
            decay = 0.85 ** years_ago
            total_decay_weight += decay
            if row["match_won_by"] == team_a:
                decayed_a += decay
            elif row["match_won_by"] == team_b:
                decayed_b += decay

        decayed_total = max(total_decay_weight, 0.01)

        rows.append(
            {
                "teamA": team_a,
                "teamB": team_b,
                "matches": matches,
                "winsA": wins_a,
                "winsB": wins_b,
                "winRateA": safe_float(wins_a / max(matches, 1)),
                "winRateB": safe_float(wins_b / max(matches, 1)),
                "recentMatches": recent_matches,
                "recentWinsA": recent_wins_a,
                "recentWinsB": recent_wins_b,
                "decayedWinRateA": safe_float(decayed_a / decayed_total),
                "decayedWinRateB": safe_float(decayed_b / decayed_total),
            }
        )

    return rows


# ---------------------------------------------------------------------------
#  Train logistic regression model weights
# ---------------------------------------------------------------------------

def train_model_weights(
    match_meta: pd.DataFrame,
    history: pd.DataFrame,
    team_stats: pd.DataFrame,
    team_venue_stats: pd.DataFrame,
    elo_ratings: dict[str, float],
    player_profiles: dict[str, dict[str, Any]],
    current_keys: set[str],
) -> dict[str, Any]:
    """Train logistic regression on historical match features to learn optimal weights."""
    current_franchises = {team["name"] for team in TEAM_META}
    team_stat_map = {row["team"]: row for _, row in team_stats.iterrows()}
    venue_stat_map: dict[str, dict[str, float]] = {}
    for _, row in team_venue_stats.iterrows():
        venue_stat_map.setdefault(str(row["team"]), {})[str(row["venue"])] = float(row.get("winRate", 0.5))

    # Build player lookup by team for batting/bowling strength
    team_players: dict[str, list[dict[str, Any]]] = {}
    for p in player_profiles.values():
        team_players.setdefault(p["currentTeam"], []).append(p)

    def team_batting_strength(team: str) -> float:
        players = sorted(team_players.get(team, []), key=lambda x: x["batting"]["index"], reverse=True)[:6]
        return np.mean([p["batting"]["index"] for p in players]) if players else 0.0

    def team_bowling_strength(team: str) -> float:
        players = sorted(team_players.get(team, []), key=lambda x: x["bowling"]["index"], reverse=True)[:5]
        return np.mean([p["bowling"]["index"] for p in players]) if players else 0.0

    def team_death_bowling(team: str) -> float:
        players = sorted(team_players.get(team, []), key=lambda x: x["bowling"].get("deathIndex", x["bowling"]["index"]), reverse=True)[:4]
        return np.mean([p["bowling"].get("deathIndex", p["bowling"]["index"]) for p in players]) if players else 0.0

    def team_powerplay_batting(team: str) -> float:
        players = sorted(team_players.get(team, []), key=lambda x: x["batting"].get("powerplayIndex", x["batting"]["index"]), reverse=True)[:3]
        return np.mean([p["batting"].get("powerplayIndex", p["batting"]["index"]) for p in players]) if players else 0.0

    def team_form(team: str) -> float:
        players = team_players.get(team, [])
        forms = [p.get("formScore", 0.5) for p in players[:11]]
        return np.mean(forms) if forms else 0.5

    features = []
    labels = []

    sorted_matches = match_meta.sort_values("date").to_dict("records")
    for row in sorted_matches:
        team_a = str(row.get("battingFirstTeam", ""))
        team_b = str(row.get("chasingTeam", ""))
        winner = str(row.get("match_won_by", ""))
        venue = str(row.get("venue", ""))
        toss_winner = str(row.get("toss_winner", ""))
        toss_decision = str(row.get("toss_decision", ""))

        if team_a not in current_franchises or team_b not in current_franchises:
            continue
        if winner not in (team_a, team_b):
            continue

        batting_edge = team_batting_strength(team_a) - team_batting_strength(team_b)
        bowling_edge = team_bowling_strength(team_a) - team_bowling_strength(team_b)
        venue_wr_a = venue_stat_map.get(team_a, {}).get(venue, 0.5)
        venue_wr_b = venue_stat_map.get(team_b, {}).get(venue, 0.5)
        venue_edge = (venue_wr_a - venue_wr_b) * 100
        elo_edge = elo_ratings.get(team_a, ELO_BASE) - elo_ratings.get(team_b, ELO_BASE)
        form_edge = team_form(team_a) - team_form(team_b)

        toss_edge = 0.0
        if toss_winner == team_a:
            toss_edge = 1.0
        elif toss_winner == team_b:
            toss_edge = -1.0

        team_a_wr = float(team_stat_map.get(team_a, {}).get("winRate", 0.5))
        team_b_wr = float(team_stat_map.get(team_b, {}).get("winRate", 0.5))
        stability_edge = team_a_wr - team_b_wr
        death_bowling_edge = team_death_bowling(team_a) - team_death_bowling(team_b)
        powerplay_edge = team_powerplay_batting(team_a) - team_powerplay_batting(team_b)

        features.append([
            batting_edge,
            bowling_edge,
            venue_edge,
            elo_edge,
            form_edge,
            toss_edge,
            stability_edge,   # proxy for h2h edge
            stability_edge,   # stability
            0.0,              # freshness (not available in historical)
            0.0,              # matchup edge (too expensive to compute per-match)
            death_bowling_edge,
            powerplay_edge,
        ])
        labels.append(1 if winner == team_a else 0)

    if len(features) < 50:
        # Not enough data — return sensible defaults
        return {
            "intercept": 0.0,
            "battingEdge": 0.35,
            "bowlingEdge": 0.30,
            "venueEdge": 0.12,
            "eloEdge": 0.25,
            "formEdge": 0.18,
            "tossEdge": 0.10,
            "h2hEdge": 0.08,
            "stabilityEdge": 0.06,
            "freshnessEdge": 0.12,
            "matchupEdge": 0.15,
            "deathBowlingEdge": 0.20,
            "powerplayEdge": 0.10,
        }

    X = np.array(features)
    y = np.array(labels)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = LogisticRegression(max_iter=2000, C=1.0, solver="lbfgs")
    model.fit(X_scaled, y)

    coefs = model.coef_[0]
    scales = scaler.scale_

    # Convert coefficients back to original scale
    raw_coefs = coefs / scales
    intercept = float(model.intercept_[0]) - float(np.sum(coefs * scaler.mean_ / scales))

    feature_names = [
        "battingEdge", "bowlingEdge", "venueEdge", "eloEdge", "formEdge",
        "tossEdge", "h2hEdge", "stabilityEdge", "freshnessEdge",
        "matchupEdge", "deathBowlingEdge", "powerplayEdge",
    ]

    weights = {"intercept": safe_float(intercept, 4)}
    for name, coef in zip(feature_names, raw_coefs):
        weights[name] = safe_float(float(coef), 4)

    return weights


# ---------------------------------------------------------------------------
#  Match-level and team-level views
# ---------------------------------------------------------------------------

def build_match_meta(history: pd.DataFrame) -> pd.DataFrame:
    innings_one = history[history["innings"] == 1].copy().sort_values(["match_id", "over", "ball_no"])
    innings_first = innings_one.groupby("match_id", as_index=False).first()[
        ["match_id", "batting_team", "bowling_team", "venue", "city"]
    ]
    innings_last = innings_one.groupby("match_id", as_index=False).last()[["match_id", "team_runs"]]
    innings_one = innings_first.merge(innings_last, on="match_id", how="left").rename(
        columns={
            "batting_team": "battingFirstTeam",
            "bowling_team": "chasingTeam",
            "team_runs": "firstInningsScore",
        }
    )
    meta = (
        history.sort_values(["match_id", "innings", "over", "ball_no"])
        .groupby("match_id", as_index=False)
        .first()[
            [
                "match_id",
                "date",
                "season",
                "year",
                "toss_winner",
                "toss_decision",
                "player_of_match",
                "match_won_by",
                "win_outcome",
            ]
        ]
    )
    meta = meta.merge(innings_one, on="match_id", how="left")
    meta["winnerIsChasing"] = meta["match_won_by"] == meta["chasingTeam"]
    meta["winnerIsBattingFirst"] = meta["match_won_by"] == meta["battingFirstTeam"]
    return meta


def build_team_views(match_meta: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    team_rows = pd.concat(
        [
            match_meta[["match_id", "date", "year", "venue", "battingFirstTeam", "match_won_by"]]
            .rename(columns={"battingFirstTeam": "team"})
            .assign(scenario="bat_first"),
            match_meta[["match_id", "date", "year", "venue", "chasingTeam", "match_won_by"]]
            .rename(columns={"chasingTeam": "team"})
            .assign(scenario="chasing"),
        ],
        ignore_index=True,
    )
    team_rows["won"] = team_rows["team"] == team_rows["match_won_by"]

    team_stats = (
        team_rows.groupby("team", as_index=False)
        .agg(matches=("match_id", "nunique"), wins=("won", "sum"))
        .assign(winRate=lambda frame: frame["wins"] / frame["matches"])
    )

    venue_stats = (
        team_rows.groupby(["team", "venue"], as_index=False)
        .agg(matches=("match_id", "nunique"), wins=("won", "sum"))
        .assign(winRate=lambda frame: frame["wins"] / frame["matches"])
    )

    return team_stats, venue_stats


def aggregate_current_players(
    history: pd.DataFrame,
    current_players: list[dict[str, Any]],
    form_scores: dict[str, float],
    position_stats: dict[str, dict[str, float]],
) -> dict[str, dict[str, Any]]:
    current_keys = {player["playerKey"] for player in current_players}
    player_lookup = {player["playerKey"]: player for player in current_players}

    player_deliveries = history[
        history["batter_key"].isin(current_keys)
        | history["bowler_key"].isin(current_keys)
        | history["player_out_key"].isin(current_keys)
    ].copy()

    batting_innings = (
        player_deliveries[player_deliveries["batter_key"].isin(current_keys)]
        .groupby(["match_id", "innings", "batter_key", "batter", "venue", "scenario", "year"], as_index=False)
        .agg(
            runs=("runs_batter", "sum"),
            balls=("valid_ball", "sum"),
            outs=("is_batter_out", "sum"),
            fours=("is_four", "sum"),
            sixes=("is_six", "sum"),
        )
    )
    batting_innings["boundaries"] = batting_innings["fours"] + batting_innings["sixes"]

    bowling_innings = (
        player_deliveries[player_deliveries["bowler_key"].isin(current_keys)]
        .groupby(["match_id", "innings", "bowler_key", "bowler", "venue", "scenario", "year"], as_index=False)
        .agg(
            balls=("valid_ball", "sum"),
            runs=("runs_bowler", "sum"),
            wickets=("is_bowler_wicket", "sum"),
            dots=("runs_total", lambda series: safe_int((series == 0).sum())),
        )
    )

    # Phase-wise aggregations
    phase_batting = (
        player_deliveries[player_deliveries["batter_key"].isin(current_keys)]
        .groupby(["batter_key", "phase"], as_index=False)
        .agg(
            runs=("runs_batter", "sum"),
            balls=("valid_ball", "sum"),
            boundaries=("is_boundary", "sum"),
            outs=("is_batter_out", "sum"),
        )
    )

    phase_bowling = (
        player_deliveries[player_deliveries["bowler_key"].isin(current_keys)]
        .groupby(["bowler_key", "phase"], as_index=False)
        .agg(
            balls=("valid_ball", "sum"),
            runs=("runs_bowler", "sum"),
            wickets=("is_bowler_wicket", "sum"),
            dots=("runs_total", lambda series: safe_int((series == 0).sum())),
        )
    )

    player_profiles: dict[str, dict[str, Any]] = {}
    for key in sorted(current_keys):
        meta = player_lookup[key]
        bat_rows = batting_innings[batting_innings["batter_key"] == key]
        bowl_rows = bowling_innings[bowling_innings["bowler_key"] == key]

        matches_played = safe_int(pd.Index(bat_rows["match_id"]).union(pd.Index(bowl_rows["match_id"])).nunique())
        batting_innings_count = safe_int(len(bat_rows))
        bowling_innings_count = safe_int(len(bowl_rows))

        batting_runs = safe_int(bat_rows["runs"].sum())
        batting_balls = safe_int(bat_rows["balls"].sum())
        batting_outs = safe_int(bat_rows["outs"].sum())
        batting_boundaries = safe_int(bat_rows["boundaries"].sum())
        recent_bat_rows = bat_rows[bat_rows["year"] >= 2024]
        chase_bat_rows = bat_rows[bat_rows["scenario"] == "chasing"]
        bat_first_rows = bat_rows[bat_rows["scenario"] == "bat_first"]

        bowling_balls = safe_int(bowl_rows["balls"].sum())
        bowling_runs = safe_int(bowl_rows["runs"].sum())
        bowling_wickets = safe_int(bowl_rows["wickets"].sum())
        bowling_dots = safe_int(bowl_rows["dots"].sum())
        recent_bowl_rows = bowl_rows[bowl_rows["year"] >= 2024]

        batting_average = batting_runs / max(batting_outs, 1)
        strike_rate = batting_runs * 100 / max(batting_balls, 1)
        boundary_rate = batting_boundaries / max(batting_balls, 1)
        recent_batting_index = (
            recent_bat_rows["runs"].sum() * 1.1 / max(recent_bat_rows["balls"].sum(), 1)
        ) * 100
        chase_index = (chase_bat_rows["runs"].sum() * 100) / max(chase_bat_rows["balls"].sum(), 1)
        bat_first_index = (bat_first_rows["runs"].sum() * 100) / max(bat_first_rows["balls"].sum(), 1)
        batting_index = (
            batting_average * 1.1
            + strike_rate * 0.22
            + boundary_rate * 100 * 0.9
            + recent_batting_index * 0.08
        )

        bowling_average = bowling_runs / max(bowling_wickets, 1)
        economy = bowling_runs * 6 / max(bowling_balls, 1)
        bowling_strike = bowling_balls / max(bowling_wickets, 1)
        dot_rate = bowling_dots / max(bowling_balls, 1)
        recent_bowling_index = recent_bowl_rows["wickets"].sum() * 24 / max(recent_bowl_rows["balls"].sum(), 1)
        bowling_index = (
            bowling_wickets * 12 / max(bowling_innings_count, 1)
            + dot_rate * 38
            - economy * 2.7
            - bowling_average * 0.15
            + recent_bowling_index * 7
        )

        # Phase-wise batting indices
        phase_bat_player = phase_batting[phase_batting["batter_key"] == key]
        phase_bat_indices = {}
        for phase_name in ("powerplay", "middle", "death"):
            phase_row = phase_bat_player[phase_bat_player["phase"] == phase_name]
            if phase_row.empty or safe_int(phase_row["balls"].sum()) < 6:
                phase_bat_indices[phase_name] = safe_float(batting_index * 0.7)
            else:
                p_runs = safe_int(phase_row["runs"].sum())
                p_balls = safe_int(phase_row["balls"].sum())
                p_boundaries = safe_int(phase_row["boundaries"].sum())
                p_outs = safe_int(phase_row["outs"].sum())
                p_sr = p_runs * 100 / max(p_balls, 1)
                p_avg = p_runs / max(p_outs, 1)
                p_br = p_boundaries / max(p_balls, 1)
                phase_bat_indices[phase_name] = safe_float(
                    bayesian_shrink(
                        p_avg * 1.0 + p_sr * 0.2 + p_br * 100 * 0.8,
                        safe_int(phase_row["balls"].sum()),
                        batting_index,
                        BAYESIAN_PRIOR_STRENGTH * 6,
                    )
                )

        # Phase-wise bowling indices
        phase_bowl_player = phase_bowling[phase_bowling["bowler_key"] == key]
        phase_bowl_indices = {}
        for phase_name in ("powerplay", "middle", "death"):
            phase_row = phase_bowl_player[phase_bowl_player["phase"] == phase_name]
            if phase_row.empty or safe_int(phase_row["balls"].sum()) < 6:
                phase_bowl_indices[phase_name] = safe_float(bowling_index * 0.7)
            else:
                p_balls = safe_int(phase_row["balls"].sum())
                p_runs = safe_int(phase_row["runs"].sum())
                p_wickets = safe_int(phase_row["wickets"].sum())
                p_dots = safe_int(phase_row["dots"].sum())
                p_econ = p_runs * 6 / max(p_balls, 1)
                p_dr = p_dots / max(p_balls, 1)
                p_innings_est = max(p_balls / 24, 1)
                raw_idx = p_wickets * 12 / p_innings_est + p_dr * 38 - p_econ * 2.7
                phase_bowl_indices[phase_name] = safe_float(
                    bayesian_shrink(
                        raw_idx,
                        p_balls,
                        bowling_index,
                        BAYESIAN_PRIOR_STRENGTH * 6,
                    )
                )

        # Venue highlights
        venue_highlights = []
        if batting_innings_count or bowling_innings_count:
            venue_batting = (
                bat_rows.groupby("venue", as_index=False)
                .agg(runs=("runs", "sum"), balls=("balls", "sum"), innings=("match_id", "count"))
                .assign(battingScore=lambda frame: frame["runs"] * 100 / frame["balls"].clip(lower=1))
            )
            venue_bowling = (
                bowl_rows.groupby("venue", as_index=False)
                .agg(
                    wickets=("wickets", "sum"),
                    balls=("balls", "sum"),
                    runs=("runs", "sum"),
                    innings=("match_id", "count"),
                )
                .assign(
                    bowlingScore=lambda frame: (
                        frame["wickets"] * 25 / frame["innings"].clip(lower=1)
                        + (frame["wickets"] * 30 / frame["balls"].clip(lower=1))
                        - (frame["runs"] * 6 / frame["balls"].clip(lower=1))
                    )
                )
            )
            merged = venue_batting.merge(venue_bowling, on="venue", how="outer", suffixes=("Bat", "Bowl")).fillna(0)
            merged["combined"] = merged["battingScore"] * 0.5 + merged["bowlingScore"] * 0.5
            venue_highlights = [
                {
                    "venue": row["venue"],
                    "battingScore": safe_float(row["battingScore"]),
                    "bowlingScore": safe_float(row["bowlingScore"]),
                    "sample": safe_int(max(row["inningsBat"], row["inningsBowl"])),
                }
                for _, row in merged.sort_values("combined", ascending=False).head(5).iterrows()
            ]

        pos_stats = position_stats.get(key, {"expectedBattingPosition": 7.0, "expectedBallsFaced": 8.0, "expectedOversBowled": 0.0})

        player_profiles[key] = {
            "name": meta["name"],
            "playerKey": key,
            "slug": meta["slug"],
            "role": meta["role"],
            "roleLabel": meta["roleLabel"],
            "currentTeam": meta["team"],
            "currentTeamId": meta["teamId"],
            "teamShortName": meta["teamShortName"],
            "teamColors": meta["teamColors"],
            "profileUrl": meta["profileUrl"],
            "imageUrl": meta["imageUrl"],
            "isCaptain": meta["isCaptain"],
            "isOverseas": meta["isOverseas"],
            "nationality": meta["nationality"],
            "bats": meta["bats"],
            "bowls": meta["bowls"],
            "matchesPlayed": matches_played,
            "batting": {
                "innings": batting_innings_count,
                "runs": batting_runs,
                "balls": batting_balls,
                "outs": batting_outs,
                "average": safe_float(batting_average),
                "strikeRate": safe_float(strike_rate),
                "boundaryRate": safe_float(boundary_rate * 100),
                "index": safe_float(max(batting_index, 0)),
                "recentIndex": safe_float(max(recent_batting_index, 0)),
                "chaseIndex": safe_float(max(chase_index, 0)),
                "batFirstIndex": safe_float(max(bat_first_index, 0)),
                "powerplayIndex": phase_bat_indices.get("powerplay", 0.0),
                "middleIndex": phase_bat_indices.get("middle", 0.0),
                "deathIndex": phase_bat_indices.get("death", 0.0),
            },
            "bowling": {
                "innings": bowling_innings_count,
                "balls": bowling_balls,
                "wickets": bowling_wickets,
                "overs": safe_float(bowling_balls / 6),
                "average": safe_float(bowling_average),
                "economy": safe_float(economy),
                "strikeRate": safe_float(bowling_strike),
                "dotRate": safe_float(dot_rate * 100),
                "index": safe_float(max(bowling_index, 0)),
                "recentIndex": safe_float(max(recent_bowling_index * 10, 0)),
                "powerplayIndex": phase_bowl_indices.get("powerplay", 0.0),
                "middleIndex": phase_bowl_indices.get("middle", 0.0),
                "deathIndex": phase_bowl_indices.get("death", 0.0),
            },
            "venueHighlights": venue_highlights,
            "formScore": form_scores.get(key, 0.5),
            "expectedBattingPosition": pos_stats["expectedBattingPosition"],
            "expectedBallsFaced": pos_stats["expectedBallsFaced"],
            "expectedOversBowled": pos_stats["expectedOversBowled"],
        }

    return player_profiles


def build_pair_stats(
    history: pd.DataFrame,
    current_player_keys: set[str],
    global_priors: dict[str, Any],
) -> list[dict[str, Any]]:
    prior_edge = global_priors.get("pairEdge", 0.0)
    prior_n = global_priors.get("priorStrength", BAYESIAN_PRIOR_STRENGTH)

    pair_rows = history[
        history["batter_key"].isin(current_player_keys) & history["bowler_key"].isin(current_player_keys)
    ].copy()
    pair_rows["dismissal"] = pair_rows["is_bowler_wicket"] & (pair_rows["player_out_key"] == pair_rows["batter_key"])

    overall = (
        pair_rows.groupby(["batter_key", "bowler_key", "batter", "bowler"], as_index=False)
        .agg(balls=("valid_ball", "sum"), runs=("runs_batter", "sum"), dismissals=("dismissal", "sum"))
    )
    overall = overall[overall["balls"] >= 6].copy()
    overall["strikeRate"] = overall["runs"] * 100 / overall["balls"].clip(lower=1)
    overall["dismissalRate"] = overall["dismissals"] / overall["balls"].clip(lower=1)
    overall["edge"] = overall["strikeRate"] * 0.14 - overall["dismissalRate"] * 140
    # Bayesian smoothed edge
    overall["smoothedEdge"] = overall.apply(
        lambda row: bayesian_shrink(row["edge"], int(row["balls"]), prior_edge, prior_n),
        axis=1,
    )

    venue = (
        pair_rows.groupby(["batter_key", "bowler_key", "batter", "bowler", "venue"], as_index=False)
        .agg(balls=("valid_ball", "sum"), runs=("runs_batter", "sum"), dismissals=("dismissal", "sum"))
    )
    venue = venue[venue["balls"] >= 4].copy()
    venue["strikeRate"] = venue["runs"] * 100 / venue["balls"].clip(lower=1)
    venue["dismissalRate"] = venue["dismissals"] / venue["balls"].clip(lower=1)
    venue["edge"] = venue["strikeRate"] * 0.15 - venue["dismissalRate"] * 150
    venue["smoothedEdge"] = venue.apply(
        lambda row: bayesian_shrink(row["edge"], int(row["balls"]), prior_edge, prior_n),
        axis=1,
    )

    records = [
        {
            "batterKey": row["batter_key"],
            "bowlerKey": row["bowler_key"],
            "batter": row["batter"],
            "bowler": row["bowler"],
            "balls": safe_int(row["balls"]),
            "runs": safe_int(row["runs"]),
            "dismissals": safe_int(row["dismissals"]),
            "strikeRate": safe_float(row["strikeRate"]),
            "dismissalRate": safe_float(row["dismissalRate"] * 100),
            "edge": safe_float(row["edge"]),
            "smoothedEdge": safe_float(row["smoothedEdge"]),
            "scope": "overall",
            "venue": None,
        }
        for _, row in overall.sort_values("balls", ascending=False).iterrows()
    ]
    records.extend(
        [
            {
                "batterKey": row["batter_key"],
                "bowlerKey": row["bowler_key"],
                "batter": row["batter"],
                "bowler": row["bowler"],
                "balls": safe_int(row["balls"]),
                "runs": safe_int(row["runs"]),
                "dismissals": safe_int(row["dismissals"]),
                "strikeRate": safe_float(row["strikeRate"]),
                "dismissalRate": safe_float(row["dismissalRate"] * 100),
                "edge": safe_float(row["edge"]),
                "smoothedEdge": safe_float(row["smoothedEdge"]),
                "scope": "venue",
                "venue": row["venue"],
            }
            for _, row in venue.sort_values(["balls", "edge"], ascending=[False, False]).iterrows()
        ]
    )
    return records


def build_venue_summary(
    match_meta: pd.DataFrame,
    current_players: dict[str, dict[str, Any]],
    global_priors: dict[str, Any],
) -> list[dict[str, Any]]:
    prior_first_innings = global_priors.get("firstInningsAverage", 165.0)
    prior_chase_wr = global_priors.get("chaseWinRate", 52.0) / 100.0
    prior_n = global_priors.get("priorStrength", BAYESIAN_PRIOR_STRENGTH)

    venue_rows = (
        match_meta.groupby("venue", as_index=False)
        .agg(
            matches=("match_id", "nunique"),
            city=("city", "first"),
            firstInningsAverage=("firstInningsScore", "mean"),
            chaseWinRate=("winnerIsChasing", "mean"),
        )
        .sort_values(["matches", "firstInningsAverage"], ascending=[False, False])
    )

    player_frame = pd.DataFrame(current_players.values())
    venues = []
    for _, venue_row in venue_rows.iterrows():
        venue_name = venue_row["venue"]
        matches_at_venue = safe_int(venue_row["matches"])

        # Bayesian shrinkage on venue stats
        smoothed_first_innings = bayesian_shrink(
            float(venue_row["firstInningsAverage"]), matches_at_venue, prior_first_innings, prior_n
        )
        smoothed_chase_wr = bayesian_shrink(
            float(venue_row["chaseWinRate"]), matches_at_venue, prior_chase_wr, prior_n
        )

        highlights = []
        for _, player_row in player_frame.iterrows():
            for item in player_row["venueHighlights"]:
                if item["venue"] == venue_name:
                    highlights.append(
                        {
                            "name": player_row["name"],
                            "team": player_row["currentTeam"],
                            "role": player_row["role"],
                            "battingScore": item["battingScore"],
                            "bowlingScore": item["bowlingScore"],
                            "combined": item["battingScore"] * 0.55 + item["bowlingScore"] * 0.45,
                        }
                    )
                    break

        venues.append(
            {
                "name": venue_name,
                "slug": slugify(venue_name),
                "city": venue_row["city"],
                "matches": matches_at_venue,
                "firstInningsAverage": safe_float(smoothed_first_innings),
                "chaseWinRate": safe_float(smoothed_chase_wr * 100),
                "topBatters": sorted(highlights, key=lambda item: item["battingScore"], reverse=True)[:5],
                "topBowlers": sorted(highlights, key=lambda item: item["bowlingScore"], reverse=True)[:5],
            }
        )

    return venues


def finalize_players(
    current_players: list[dict[str, Any]],
    player_profiles: dict[str, dict[str, Any]],
    team_stats: pd.DataFrame,
    usage_map: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    team_stat_map = {row["team"]: row for _, row in team_stats.iterrows()}
    output = []

    for raw_player in current_players:
        key = raw_player["playerKey"]
        player = player_profiles[key]
        usage = usage_map.get((raw_player["team"], key), {})
        team_win_rate = team_stat_map.get(raw_player["team"], {}).get("winRate", 0.5)
        availability = AVAILABILITY_OVERRIDES.get((raw_player["team"], key), 1.0)

        current_season_bonus = (
            safe_int(usage.get("starts2026")) * 12
            + safe_int(usage.get("recentStarts2026")) * 8
            + safe_int(usage.get("impactMatches2026")) * 3
            + safe_float(usage.get("recencyScore"), 3) * 18
        )
        if not safe_int(usage.get("matches2026")):
            current_season_bonus -= 6

        selection_score = (
            22
            + player["matchesPlayed"] * 0.22
            + player["batting"]["index"] * 0.12
            + player["bowling"]["index"] * 0.12
            + player["batting"]["recentIndex"] * 0.05
            + player["bowling"]["recentIndex"] * 0.03
            + (8 if raw_player["isCaptain"] else 0)
            + (5 if raw_player["role"] == "Wicketkeeper" else 0)
            + team_win_rate * 12
            + current_season_bonus
            + player.get("formScore", 0.5) * 15  # Form bonus
        ) * availability

        base_points = (
            player["batting"]["index"] * 0.32
            + player["bowling"]["index"] * 0.38
            + player["batting"]["recentIndex"] * 0.08
            + player["bowling"]["recentIndex"] * 0.06
            + player["matchesPlayed"] * 0.15
            + current_season_bonus * 0.45
            + player.get("formScore", 0.5) * 10  # Form bonus
        )

        fantasy_credit = min(10.5, max(6.5, 6.3 + base_points / 20))
        player["selectionScore"] = safe_float(selection_score)
        player["availability"] = safe_float(availability)
        player["baseFantasyPoints"] = safe_float(base_points)
        player["fantasyCredit"] = safe_float(fantasy_credit)
        player["matches2026"] = safe_int(usage.get("matches2026"))
        player["starts2026"] = safe_int(usage.get("starts2026"))
        player["impactMatches2026"] = safe_int(usage.get("impactMatches2026"))
        player["recentStarts2026"] = safe_int(usage.get("recentStarts2026"))
        player["lastSeen"] = usage.get("lastSeen", "")
        player["recencyScore"] = safe_float(usage.get("recencyScore"))
        output.append(player)

    return output


def build_teams(
    players: list[dict[str, Any]],
    team_stats: pd.DataFrame,
    team_venue_stats: pd.DataFrame,
    elo_ratings: dict[str, float],
) -> list[dict[str, Any]]:
    team_stat_map = {row["team"]: row for _, row in team_stats.iterrows()}
    venue_map: dict[str, list[dict[str, Any]]] = {}
    for _, row in team_venue_stats.iterrows():
        venue_map.setdefault(row["team"], []).append(
            {
                "venue": row["venue"],
                "matches": safe_int(row["matches"]),
                "winRate": safe_float(row["winRate"] * 100),
            }
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for player in players:
        grouped.setdefault(player["currentTeam"], []).append(player)

    teams = []
    for team in TEAM_META:
        squad = sorted(
            grouped.get(team["name"], []),
            key=lambda item: (item["selectionScore"], item["starts2026"], item["baseFantasyPoints"]),
            reverse=True,
        )
        batting_rating = safe_float(
            sum(item["batting"]["index"] for item in squad[:8]) / max(min(len(squad), 8), 1)
        )
        bowling_rating = safe_float(
            sum(item["bowling"]["index"] for item in squad[:7]) / max(min(len(squad), 7), 1)
        )
        team_row = team_stat_map.get(team["name"], {})

        teams.append(
            {
                **team,
                "matches": safe_int(team_row.get("matches", 0)),
                "wins": safe_int(team_row.get("wins", 0)),
                "winRate": safe_float(team_row.get("winRate", 0) * 100),
                "battingRating": batting_rating,
                "bowlingRating": bowling_rating,
                "eloRating": safe_float(elo_ratings.get(team["name"], ELO_BASE)),
                "venueStats": sorted(
                    venue_map.get(team["name"], []),
                    key=lambda item: (item["matches"], item["winRate"]),
                    reverse=True,
                )[:12],
                "squad": squad,
            }
        )

    return teams


def build_dashboard(
    match_meta: pd.DataFrame,
    players: list[dict[str, Any]],
    venues: list[dict[str, Any]],
    official_history: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "matches": safe_int(match_meta["match_id"].nunique()),
        "season2026Matches": safe_int(official_history["match_id"].nunique()) if not official_history.empty else 0,
        "seasons": safe_int(match_meta["season"].nunique()),
        "venues": safe_int(len(venues)),
        "currentPlayers": safe_int(len(players)),
        "historyWindow": f"2008-{CURRENT_SEASON}",
    }


def write_duckdb(
    history: pd.DataFrame,
    match_meta: pd.DataFrame,
    teams: list[dict[str, Any]],
    players: list[dict[str, Any]],
    venues: list[dict[str, Any]],
    pair_stats: list[dict[str, Any]],
    head_to_head: list[dict[str, Any]],
    fixtures: list[dict[str, Any]],
) -> None:
    connection = duckdb.connect(str(DUCKDB_PATH))
    connection.register("history_frame", history)
    connection.register("matches_frame", match_meta)
    connection.register("teams_frame", pd.DataFrame(teams))
    connection.register("players_frame", pd.DataFrame(players))
    connection.register("venues_frame", pd.DataFrame(venues))
    connection.register("pairs_frame", pd.DataFrame(pair_stats))
    connection.register("head_to_head_frame", pd.DataFrame(head_to_head))
    connection.register("fixtures_frame", pd.DataFrame(fixtures))

    connection.execute("CREATE OR REPLACE TABLE deliveries AS SELECT * FROM history_frame")
    connection.execute("CREATE OR REPLACE TABLE matches AS SELECT * FROM matches_frame")
    connection.execute("CREATE OR REPLACE TABLE teams AS SELECT * FROM teams_frame")
    connection.execute("CREATE OR REPLACE TABLE players AS SELECT * FROM players_frame")
    connection.execute("CREATE OR REPLACE TABLE venues AS SELECT * FROM venues_frame")
    connection.execute("CREATE OR REPLACE TABLE pair_stats AS SELECT * FROM pairs_frame")
    connection.execute("CREATE OR REPLACE TABLE head_to_head AS SELECT * FROM head_to_head_frame")
    connection.execute("CREATE OR REPLACE TABLE fixtures AS SELECT * FROM fixtures_frame")
    connection.close()


def main() -> None:
    ensure_dirs()

    print("Scraping current squads...")
    scraped_squads = current_squads()
    print("Fetching official 2026 data...")
    official_history_raw, official_meta, usage_map, fixtures = fetch_official_2026_data()
    squads = merge_current_squad_context(scraped_squads, official_meta)

    print("Loading Kaggle history...")
    kaggle_history = load_kaggle_history()

    # FIX: Only prepare official data once, then concat raw frames
    if not official_history_raw.empty:
        official_history = prepare_history_frame(official_history_raw)
        history = pd.concat([kaggle_history, official_history], ignore_index=True, sort=False)
    else:
        official_history = official_history_raw
        history = kaggle_history.copy()

    print("Building match metadata...")
    match_meta = build_match_meta(history)
    team_stats, team_venue_stats = build_team_views(match_meta)

    print("Computing Elo ratings...")
    elo_ratings = compute_elo_ratings(match_meta)

    current_keys = {player["playerKey"] for player in squads}

    print("Computing EWMA form scores...")
    form_scores = compute_ewma_form(history, current_keys)

    print("Computing batting position stats...")
    position_stats = compute_batting_position_stats(history, current_keys)

    print("Aggregating player profiles...")
    player_profiles = aggregate_current_players(history, squads, form_scores, position_stats)

    print("Computing global priors...")
    # Need a raw pair_stats df for global priors
    pair_rows_for_priors = history[
        history["batter_key"].isin(current_keys) & history["bowler_key"].isin(current_keys)
    ]
    global_priors = compute_global_priors(player_profiles, match_meta, pair_rows_for_priors)

    print("Building pair stats with Bayesian shrinkage...")
    pair_stats = build_pair_stats(history, current_keys, global_priors)

    print("Finalizing players...")
    players = finalize_players(squads, player_profiles, team_stats, usage_map)

    print("Building venue summary with Bayesian shrinkage...")
    venues = build_venue_summary(match_meta, {player["playerKey"]: player for player in players}, global_priors)

    print("Building team profiles with Elo...")
    teams = build_teams(players, team_stats, team_venue_stats, elo_ratings)

    print("Building decayed head-to-head...")
    head_to_head = build_decayed_head_to_head(match_meta)

    print("Training logistic regression model weights...")
    model_weights = train_model_weights(
        match_meta, history, team_stats, team_venue_stats,
        elo_ratings, player_profiles, current_keys,
    )

    dashboard = build_dashboard(match_meta, players, venues, official_history)

    app_data = {
        "builtAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "historical": f"Kaggle {KAGGLE_DATASET}",
            "currentSquads": "Official IPL 2026 team pages + official IPL scores feeds",
            "notes": [
                "Historical features are trained from the Kaggle IPL 2008-2025 dataset.",
                "Current-player form, current squads, and probable-XI signals are refreshed from official IPL 2026 feeds.",
                "Batter-vs-bowler matchup edges are stored both overall and venue-specific with Bayesian shrinkage.",
                "Dream XI uses a local fantasy model and proxy credits, not official contest credits.",
                "Win probability uses logistic regression weights trained on historical match features.",
                "Elo ratings, EWMA form scores, and phase-wise stats (PP/middle/death) inform predictions.",
                "Venue stats use Bayesian smoothing to prevent small-sample bias.",
                "Head-to-head records are weighted by recency (0.85^years_ago decay).",
            ],
        },
        "dashboard": dashboard,
        "fixtures": fixtures,
        "teams": teams,
        "players": players,
        "venues": venues,
        "pairStats": pair_stats,
        "headToHead": head_to_head,
        "modelWeights": model_weights,
        "globalPriors": global_priors,
    }

    write_duckdb(history, match_meta, teams, players, venues, pair_stats, head_to_head, fixtures)
    write_json(APP_DATA_PATH, app_data)

    print(f"\nWrote app data to {APP_DATA_PATH}")
    print(f"Wrote DuckDB database to {DUCKDB_PATH}")
    print(f"\nElo ratings: {json.dumps(elo_ratings, indent=2)}")
    print(f"Model weights: {json.dumps(model_weights, indent=2)}")
    print(f"Global priors: {json.dumps(global_priors, indent=2)}")


if __name__ == "__main__":
    main()

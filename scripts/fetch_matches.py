#!/usr/bin/env python3
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_page(status):
    url = f"https://www.rolandgarros.com/fr-fr/matches?status={status}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def extract_nuxt_data(html):
    soup = BeautifulSoup(html, "html.parser")

    # Nuxt 3: <script id="__NUXT_DATA__" type="application/json">
    tag = soup.find("script", {"id": "__NUXT_DATA__"})
    if tag and tag.string:
        try:
            return json.loads(tag.string)
        except json.JSONDecodeError:
            pass

    # Nuxt 2: window.__NUXT__ = (function(...){...}(args))
    for script in soup.find_all("script"):
        text = script.string or ""
        if "window.__NUXT__" not in text:
            continue
        node_script = f"{text}\nprocess.stdout.write(JSON.stringify(window.__NUXT__));"
        result = subprocess.run(
            ["node", "-e", node_script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

    return None


def find_matches(obj, found=None):
    if found is None:
        found = []
    if isinstance(obj, dict):
        if "teamA" in obj and "teamB" in obj and "matchData" in obj:
            found.append(obj)
        else:
            for v in obj.values():
                find_matches(v, found)
    elif isinstance(obj, list):
        for item in obj:
            find_matches(item, found)
    return found


def format_player_name(player):
    first = player.get("firstName", "")
    last = player.get("lastName", "")
    if first:
        return f"{first[0]}. {last}"
    return last


def format_team(team, players):
    name = " / ".join(format_player_name(p) for p in players)
    countries = list(dict.fromkeys(p.get("country", "") for p in players))
    seed = team.get("seed")
    raw_sets = team.get("sets", [])
    sets = [s.get("value", s) if isinstance(s, dict) else s for s in raw_sets]
    return {
        "name": name,
        "country": "/".join(countries),
        "seed": seed if seed and seed > 0 else None,
        "sets": sets,
        "winner": team.get("winner", False),
        "points": team.get("points"),
        "has_service": team.get("hasService", False),
    }


def format_match(raw, status):
    md = raw.get("matchData", {})
    team_a = raw.get("teamA", {})
    team_b = raw.get("teamB", {})
    players_a = team_a.get("players", [])
    players_b = team_b.get("players", [])

    return {
        "id": raw.get("id", ""),
        "status": status,
        "round": md.get("roundLabel", ""),
        "court": md.get("courtName", ""),
        "time": md.get("notBefore", ""),
        "date": md.get("dateSchedule", ""),
        "is_night": md.get("isNightSession", False),
        "team_a": format_team(team_a, players_a),
        "team_b": format_team(team_b, players_b),
    }


def main():
    all_matches = []
    seen_ids = set()

    for status in ["inprogress", "upcoming", "completed"]:
        try:
            html = fetch_page(status)
            data = extract_nuxt_data(html)
            if not data:
                print(f"[{status}] no NUXT data found", file=sys.stderr)
                continue
            raw_matches = find_matches(data)
            for raw in raw_matches:
                mid = raw.get("id", "")
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)
                all_matches.append(format_match(raw, status))
            print(f"[{status}] {len(raw_matches)} matches found")
        except Exception as e:
            print(f"[{status}] error: {e}", file=sys.stderr)

    live = [m for m in all_matches if m["status"] == "inprogress"]
    upcoming = [m for m in all_matches if m["status"] == "upcoming"]
    completed = [m for m in all_matches if m["status"] == "completed"]

    output = {
        "updated_at": datetime.now(timezone.utc).strftime("%d/%m %H:%M"),
        "live_count": len(live),
        "matches": live + upcoming + completed,
    }

    out_path = Path(__file__).parent.parent / "data" / "matches.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"Done — {len(all_matches)} total matches written to {out_path}")


if __name__ == "__main__":
    main()

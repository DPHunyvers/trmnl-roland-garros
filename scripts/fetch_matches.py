#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import tempfile
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
    # Nuxt 3: <script id="__NUXT_DATA__" type="application/json">
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", {"id": "__NUXT_DATA__"})
    if tag:
        text = tag.get_text() or ""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Nuxt 2: window.__NUXT__ = (function(...){...}(args))
    # Use regex on raw HTML — BeautifulSoup's .string returns None on large scripts
    match = re.search(r"window\.__NUXT__\s*=\s*(\(function\(.*?</script>)", html, re.DOTALL)
    if not match:
        return None

    nuxt_block = match.group(1)
    # Strip the trailing </script>
    nuxt_expr = nuxt_block[:nuxt_block.rfind("</script>")].strip().rstrip(";")
    node_script = f"var __data = {nuxt_expr};\nprocess.stdout.write(JSON.stringify(__data));"

    tmp = tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False, encoding="utf-8")
    try:
        tmp.write(node_script)
        tmp.close()
        result = subprocess.run(
            ["node", tmp.name],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        os.unlink(tmp.name)

    if result.returncode != 0:
        print(f"node error: {result.stderr[:300]}", file=sys.stderr)
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}", file=sys.stderr)
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

    def has_french_player(m):
        return (
            m["team_a"]["country"] == "FRA"
            or m["team_b"]["country"] == "FRA"
            or "/FRA" in m["team_a"]["country"]
            or "/FRA" in m["team_b"]["country"]
        )

    french_matches = [m for m in live + upcoming if has_french_player(m)][:4]

    output = {
        "updated_at": datetime.now(timezone.utc).strftime("%d/%m %H:%M"),
        "live_count": len(live),
        "matches": live + upcoming + completed,
        "french_matches": french_matches,
    }

    out_path = Path(__file__).parent.parent / "data" / "matches.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"Done — {len(all_matches)} total matches written to {out_path}")


if __name__ == "__main__":
    main()

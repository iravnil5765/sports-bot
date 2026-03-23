"""
Fetches injury reports from ESPN's unofficial public API.
No API key required.
"""

import aiohttp
import asyncio

ESPN_INJURY_URLS = {
    'basketball_nba':       'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries',
    'americanfootball_nfl': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries',
    'icehockey_nhl':        'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries',
    'baseball_mlb':         'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries',
}

_cache = {}   # sport_key -> list of injury dicts


async def fetch_injuries(sport_key):
    if sport_key not in ESPN_INJURY_URLS:
        return []
    if sport_key in _cache:
        return _cache[sport_key]

    url = ESPN_INJURY_URLS[sport_key]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()

        injuries = []
        for team in data.get('injuries', []):
            team_name = team.get('team', {}).get('displayName', '')
            for player in team.get('injuries', []):
                status = player.get('status', '')
                name   = player.get('athlete', {}).get('displayName', '')
                detail = player.get('shortComment', player.get('longComment', ''))
                if status.upper() in ('OUT', 'DOUBTFUL', 'QUESTIONABLE'):
                    injuries.append({
                        'team':   team_name,
                        'player': name,
                        'status': status,
                        'detail': detail,
                    })

        _cache[sport_key] = injuries
        return injuries

    except Exception:
        return []


async def get_team_injuries(sport_key, team_name):
    """Return injuries for a specific team (fuzzy match on team name)."""
    all_injuries = await fetch_injuries(sport_key)
    team_lower   = team_name.lower()
    return [i for i in all_injuries
            if any(word in i['team'].lower() for word in team_lower.split())]


def format_injury_alert(injuries):
    """Format injuries into a short alert string."""
    if not injuries:
        return None
    out      = [i for i in injuries if i['status'].upper() == 'OUT']
    doubtful = [i for i in injuries if i['status'].upper() == 'DOUBTFUL']
    lines    = []
    if out:
        names = ', '.join(i['player'] for i in out[:3])
        lines.append(f"❌ OUT: {names}")
    if doubtful:
        names = ', '.join(i['player'] for i in doubtful[:2])
        lines.append(f"⚠️ DOUBTFUL: {names}")
    return ' | '.join(lines) if lines else None


def clear_cache():
    _cache.clear()

import aiohttp
import asyncio
import config

BASE = "https://api.the-odds-api.com/v4"


async def _get(url, params):
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            print(f"[OddsAPI] {resp.status} for {url}")
            return []


async def get_odds(sport_key):
    return await _get(
        f"{BASE}/sports/{sport_key}/odds",
        {
            'apiKey':      config.ODDS_API_KEY,
            'regions':     'us',
            'markets':     'h2h,spreads,totals',
            'oddsFormat':  'decimal',
            'dateFormat':  'iso',
        }
    )


async def get_scores(sport_key):
    return await _get(
        f"{BASE}/sports/{sport_key}/scores",
        {
            'apiKey':   config.ODDS_API_KEY,
            'daysFrom': 2,
        }
    )


async def fetch_all_odds():
    """Fetch odds across all active sports."""
    all_games = []
    for sport in config.ACTIVE_SPORTS:
        games = await get_odds(sport)
        for g in games:
            g['sport_key'] = sport
        all_games.extend(games)
        await asyncio.sleep(0.3)   # stay well within rate limits
    return all_games

"""
Value-bet picker — NZD / Betcha edition.

Algorithm:
  1. Pull odds for every bookmaker available.
  2. Average across books to get consensus (de-vigged) probability.
  3. Best available price vs consensus → Expected Value.
  4. Filter: EV >= MIN_EV_THRESHOLD AND true_prob >= MIN_WIN_PROBABILITY.
  5. Assign confidence tier and unit size.
  6. Rank by EV, cap at MAX_DAILY_PICKS, one pick per game.
"""

import re
from datetime import datetime, timezone, timedelta

import config
from odds_api import fetch_all_odds
from injuries import get_team_injuries, format_injury_alert


# ── Odds math ──────────────────────────────────────────────────────────────────

def to_decimal(dec):
    return round(dec, 2)


def implied(dec):
    return 1 / dec


def devig(odds_list):
    probs = [implied(o) for o in odds_list]
    total = sum(probs)
    return [p / total for p in probs]


def ev(true_prob, dec_odds):
    return true_prob * dec_odds - 1


def get_confidence(true_prob):
    if true_prob >= 0.85:
        return 'ELITE'
    if true_prob >= 0.75:
        return 'STRONG'
    if true_prob >= 0.60:
        return 'GOOD'
    return 'SKIP'


def kelly_units(edge, dec_odds, true_prob):
    """
    Quarter-Kelly, bucketed into 0.5 / 1 / 2u tiers.
    Never exceeds config.MAX_UNITS_PER_BET.
    """
    b = dec_odds - 1
    q = 1 - true_prob
    if b <= 0 or true_prob <= 0:
        return 0
    full_kelly = (b * true_prob - q) / b
    frac = full_kelly * 0.25

    if frac <= 0:
        return 0
    if frac < 0.01:
        return 0.5
    if frac < 0.02:
        return 1
    return min(2, config.MAX_UNITS_PER_BET)


# ── Market analysis ────────────────────────────────────────────────────────────

def _collect_market(game, market_key):
    by_key = {}
    for bk in game.get('bookmakers', []):
        for market in bk.get('markets', []):
            if market['key'] != market_key:
                continue
            for outcome in market.get('outcomes', []):
                name  = outcome['name']
                price = outcome['price']
                point = outcome.get('point')
                okey  = f"{name}_{point}" if point is not None else name

                if okey not in by_key:
                    by_key[okey] = {
                        'name':       name,
                        'label':      _make_label(market_key, name, point),
                        'market_key': market_key,
                        'point':      point,
                        'all_prices': [],
                        'best_price': 0,
                    }
                by_key[okey]['all_prices'].append(price)
                if price > by_key[okey]['best_price']:
                    by_key[okey]['best_price'] = price
    return by_key


def _make_label(market_key, name, point):
    if market_key == 'h2h':
        return 'Moneyline'
    if market_key == 'spreads':
        sign = '+' if point and point > 0 else ''
        return f"Spread {sign}{point}"
    if market_key == 'totals':
        return f"{name} {point}"
    return market_key.upper()


def _analyze_2way(by_key, game, sport, commence_time, home_injuries, away_injuries):
    keys = list(by_key.keys())
    if len(keys) != 2:
        return []

    k1, k2   = keys
    odds1    = by_key[k1]['all_prices']
    odds2    = by_key[k2]['all_prices']
    if len(odds1) < 2 or len(odds2) < 2:
        return []

    avg1     = sum(odds1) / len(odds1)
    avg2     = sum(odds2) / len(odds2)
    tp1, tp2 = devig([avg1, avg2])

    candidates = []
    for key, tp, data in [(k1, tp1, by_key[k1]), (k2, tp2, by_key[k2])]:
        if tp < config.MIN_WIN_PROBABILITY:
            continue

        best  = data['best_price']
        edge  = ev(tp, best)
        if edge < config.MIN_EV_THRESHOLD:
            continue

        units = kelly_units(edge, best, tp)
        if units <= 0:
            continue

        confidence = get_confidence(tp)
        if confidence == 'SKIP':
            continue

        # Work out which team this is and pull injuries
        bet_name = data['name']
        if bet_name == game['home_team']:
            injury_alert = format_injury_alert(home_injuries)
        elif bet_name == game['away_team']:
            injury_alert = format_injury_alert(away_injuries)
        else:
            injury_alert = None

        # Skip bet if key players are OUT for this team
        if injury_alert and '❌ OUT' in injury_alert:
            continue

        stake_nzd  = units * config.UNIT_SIZE_NZD
        return_nzd = stake_nzd * best

        candidates.append({
            'sport':         sport,
            'home_team':     game['home_team'],
            'away_team':     game['away_team'],
            'bet_type':      data['market_key'].upper(),
            'bet_on':        data['name'],
            'bet_label':     data['label'],
            'odds':          best,
            'units':         units,
            'stake_nzd':     stake_nzd,
            'return_nzd':    return_nzd,
            'ev':            edge,
            'true_prob':     tp,
            'implied_prob':  implied(best),
            'confidence':    confidence,
            'injury_alert':  injury_alert,
            'game_id':       game['id'],
            'commence_time': commence_time,
            'point':         data.get('point'),
        })
    return candidates


# ── Betcha navigation path ─────────────────────────────────────────────────────

def betcha_path(pick):
    sport_map = {
        'basketball_nba':           'NBA',
        'americanfootball_nfl':     'NFL',
        'icehockey_nhl':            'NHL',
        'baseball_mlb':             'MLB',
        'soccer_epl':               'Soccer → EPL',
        'soccer_australia_aleague': 'Soccer → A-League',
        'mma_mixed_martial_arts':   'UFC/MMA',
    }
    sport_nav = sport_map.get(pick['sport'], pick['sport'])
    bet_type  = pick['bet_label']
    return (f"Betcha → {sport_nav} → "
            f"{pick['away_team']} vs {pick['home_team']} → "
            f"{bet_type} → {pick['bet_on']}")


# ── Main entry point ───────────────────────────────────────────────────────────

async def find_value_picks():
    games    = await fetch_all_odds()
    now      = datetime.now(timezone.utc)
    cutoff   = now + timedelta(hours=4)

    candidates = []

    for game in games:
        try:
            ct = datetime.fromisoformat(game['commence_time'].replace('Z', '+00:00'))
        except Exception:
            continue
        if ct < cutoff:
            continue

        sport = game['sport_key']
        home  = game['home_team']
        away  = game['away_team']

        # Fetch injuries for both teams
        home_injuries, away_injuries = [], []
        try:
            home_injuries = await get_team_injuries(sport, home)
            away_injuries = await get_team_injuries(sport, away)
        except Exception:
            pass

        for mkt in ('h2h', 'spreads', 'totals'):
            by_key = _collect_market(game, mkt)
            hits   = _analyze_2way(by_key, game, sport, ct, home_injuries, away_injuries)
            candidates.extend(hits)

    candidates.sort(key=lambda x: x['ev'], reverse=True)

    seen, picks = set(), []
    for c in candidates:
        if c['game_id'] not in seen:
            seen.add(c['game_id'])
            picks.append(c)
        if len(picks) >= config.MAX_DAILY_PICKS:
            break

    return picks


# ── Result grading ─────────────────────────────────────────────────────────────

def _extract_number(s):
    m = re.search(r'([+-]?\d+\.?\d*)', str(s))
    return float(m.group(1)) if m else None


def determine_result(pick, game_data):
    if not game_data.get('completed'):
        return None

    scores    = game_data.get('scores') or []
    score_map = {s['name']: float(s['score']) for s in scores}
    home      = game_data.get('home_team', '')
    away      = game_data.get('away_team', '')
    hs        = score_map.get(home)
    as_       = score_map.get(away)
    if hs is None or as_ is None:
        return None

    bet_on   = pick['bet_on']
    bet_type = pick['bet_type']

    if bet_type == 'H2H':
        if hs > as_:
            winner = home
        elif as_ > hs:
            winner = away
        else:
            return 'VOID'
        return 'WIN' if bet_on == winner else 'LOSS'

    elif bet_type == 'SPREADS':
        point = pick.get('point') or _extract_number(pick.get('bet_label', ''))
        if point is None:
            return None
        ref, opp = (hs, as_) if bet_on == home else (as_, hs)
        margin   = ref + point - opp
        if margin > 0:   return 'WIN'
        if margin == 0:  return 'VOID'
        return 'LOSS'

    elif bet_type == 'TOTALS':
        total = hs + as_
        point = _extract_number(pick.get('bet_label', ''))
        if point is None:
            return None
        if 'Over' in bet_on:
            return 'WIN' if total > point else ('VOID' if total == point else 'LOSS')
        else:
            return 'WIN' if total < point else ('VOID' if total == point else 'LOSS')

    return None

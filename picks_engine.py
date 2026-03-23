"""
Value-bet picker.

Algorithm:
  1. Pull odds for every bookmaker available.
  2. Average the odds across books to estimate "consensus" probability (de-vigged).
  3. The best available price for each side is compared against that consensus.
  4. If best_price implies worse-than-consensus prob → the market is giving us value.
  5. Rank by Expected Value, cap at MAX_DAILY_PICKS, one pick per game.
"""

import re
from datetime import datetime, timezone, timedelta

import config
from odds_api import fetch_all_odds


# ── Odds math ─────────────────────────────────────────────────────────────────

def to_american(dec):
    if dec >= 2.0:
        return f"+{int((dec - 1) * 100)}"
    return f"-{int(100 / (dec - 1))}"


def implied(dec):
    return 1 / dec


def devig(odds_list):
    """Remove bookmaker margin; return true probabilities that sum to 1."""
    probs = [implied(o) for o in odds_list]
    total = sum(probs)
    return [p / total for p in probs]


def ev(true_prob, dec_odds):
    """Expected value as a fraction. 0.05 → 5% edge."""
    return true_prob * dec_odds - 1


def kelly_units(edge, dec_odds):
    """Quarter-Kelly unit sizing, bucketed into 1 / 2 / 3 units."""
    b = dec_odds - 1
    p = (edge + 1) / dec_odds          # back-solve: what win-prob gives this EV?
    q = 1 - p
    if b <= 0 or p <= 0:
        return 0
    full_kelly = (b * p - q) / b
    frac = full_kelly * 0.25            # fractional Kelly for safety
    if frac <= 0:
        return 0
    if frac < 0.015:
        return 1
    if frac < 0.025:
        return 2
    return 3


# ── Market analysis ───────────────────────────────────────────────────────────

def _analyze_2way(outcomes_by_key, game, sport, commence_time):
    """
    Analyze a 2-outcome market (h2h or totals).
    Returns a list of candidate pick dicts.
    """
    keys = list(outcomes_by_key.keys())
    if len(keys) != 2:
        return []

    k1, k2 = keys
    odds1 = outcomes_by_key[k1]['all_prices']
    odds2 = outcomes_by_key[k2]['all_prices']

    # Need at least 2 books per side for a reliable consensus
    if len(odds1) < 2 or len(odds2) < 2:
        return []

    avg1 = sum(odds1) / len(odds1)
    avg2 = sum(odds2) / len(odds2)
    tp1, tp2 = devig([avg1, avg2])

    candidates = []
    for key, tp, data in [(k1, tp1, outcomes_by_key[k1]),
                          (k2, tp2, outcomes_by_key[k2])]:
        best = data['best_price']
        edge = ev(tp, best)
        if edge >= config.MIN_EV_THRESHOLD:
            units = kelly_units(edge, best)
            if units > 0:
                candidates.append({
                    'sport':          sport,
                    'home_team':      game['home_team'],
                    'away_team':      game['away_team'],
                    'bet_type':       data['market_key'].upper(),
                    'bet_on':         data['name'],
                    'bet_label':      data['label'],
                    'odds':           best,
                    'units':          units,
                    'ev':             edge,
                    'true_prob':      tp,
                    'game_id':        game['id'],
                    'commence_time':  commence_time,
                    'point':          data.get('point'),
                })
    return candidates


def _collect_market(game, market_key):
    """
    Walk all bookmakers and collect, per outcome-key:
      - all prices seen across books
      - the best (highest) price
      - display name and label
    """
    by_key = {}

    for bk in game.get('bookmakers', []):
        for market in bk.get('markets', []):
            if market['key'] != market_key:
                continue
            for outcome in market.get('outcomes', []):
                name  = outcome['name']
                price = outcome['price']
                point = outcome.get('point')

                # unique key per outcome (e.g. "Over_215.5")
                okey = f"{name}_{point}" if point is not None else name

                if okey not in by_key:
                    label = _make_label(market_key, name, point)
                    by_key[okey] = {
                        'name':        name,
                        'label':       label,
                        'market_key':  market_key,
                        'point':       point,
                        'all_prices':  [],
                        'best_price':  0,
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


# ── Main entry point ──────────────────────────────────────────────────────────

async def find_value_picks():
    """Return up to MAX_DAILY_PICKS value-bet picks, sorted by EV desc."""
    games = await fetch_all_odds()

    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=4)   # ignore games starting in < 4 hours

    candidates = []

    for game in games:
        try:
            ct = datetime.fromisoformat(game['commence_time'].replace('Z', '+00:00'))
        except Exception:
            continue

        if ct < cutoff:
            continue

        sport = game['sport_key']

        for mkt in ('h2h', 'spreads', 'totals'):
            by_key = _collect_market(game, mkt)

            # 2-way markets only (covers h2h and O/U totals)
            hits = _analyze_2way(by_key, game, sport, ct)
            candidates.extend(hits)

    # Best EV first, one pick per game
    candidates.sort(key=lambda x: x['ev'], reverse=True)
    seen, picks = set(), []
    for c in candidates:
        if c['game_id'] not in seen:
            seen.add(c['game_id'])
            picks.append(c)
        if len(picks) >= config.MAX_DAILY_PICKS:
            break

    return picks


# ── Result grading helpers ────────────────────────────────────────────────────

def _extract_number(s):
    m = re.search(r'([+-]?\d+\.?\d*)', str(s))
    return float(m.group(1)) if m else None


def determine_result(pick, game_data):
    """
    Return 'WIN' / 'LOSS' / 'VOID' / None (can't determine yet).
    pick      — dict from database
    game_data — one entry from the scores endpoint
    """
    if not game_data.get('completed'):
        return None

    scores = game_data.get('scores') or []
    score_map = {s['name']: float(s['score']) for s in scores}

    home = game_data.get('home_team', '')
    away = game_data.get('away_team', '')

    hs = score_map.get(home)
    as_ = score_map.get(away)
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
        ref_team = home if bet_on == home else away
        ref_score, opp_score = (hs, as_) if ref_team == home else (as_, hs)
        margin = ref_score + point - opp_score
        if margin > 0:
            return 'WIN'
        if margin == 0:
            return 'VOID'
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

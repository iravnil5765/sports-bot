import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
ODDS_API_KEY  = os.getenv('ODDS_API_KEY')

PICKS_CHANNEL_ID   = int(os.getenv('PICKS_CHANNEL_ID', 0))
RESULTS_CHANNEL_ID = int(os.getenv('RESULTS_CHANNEL_ID', 0))

# ── Bankroll (NZD) ─────────────────────────────────────────────────────────────
CURRENCY         = 'NZD'
CURRENCY_SYMBOL  = '$'
STARTING_BANKROLL = float(os.getenv('STARTING_BANKROLL', 100))
UNIT_SIZE_NZD    = 5.0          # 1 unit = $5 NZD

# ── Risk management (disabled — paper trading mode) ───────────────────────────
DAILY_EXPOSURE_LIMIT = 999999.0
DAILY_STOP_LOSS      = 999999.0
MAX_UNITS_PER_BET    = 3
MIN_WIN_PROBABILITY  = 0.52     # Very low filter — more picks

# ── Pick settings ──────────────────────────────────────────────────────────────
MAX_DAILY_PICKS   = 10
MIN_EV_THRESHOLD  = 0.01        # 1% edge minimum — casts a wide net

# ── Confidence tiers ───────────────────────────────────────────────────────────
# Based on true win probability
CONFIDENCE = {
    'ELITE':  (0.85, '🟢 ELITE',  '00AA44'),
    'STRONG': (0.75, '🟢 STRONG', '44BB44'),
    'GOOD':   (0.60, '🟡 GOOD',   'DDAA00'),
    'SKIP':   (0.00, '🔴 SKIP',   'CC0000'),
}

# ── Sports to monitor ──────────────────────────────────────────────────────────
ACTIVE_SPORTS = [
    'basketball_nba',
    'soccer_epl',
    'icehockey_nhl',
]

SPORT_DISPLAY = {
    'basketball_nba':              '🏀 NBA',
    'basketball_ncaab':            '🏀 NCAAB',
    'americanfootball_nfl':        '🏈 NFL',
    'icehockey_nhl':               '🏒 NHL',
    'baseball_mlb':                '⚾ MLB',
    'soccer_epl':                  '⚽ EPL',
    'soccer_australia_aleague':    '⚽ A-League',
    'soccer_mls':                  '⚽ MLS',
    'mma_mixed_martial_arts':      '🥊 UFC/MMA',
}

# Google Sheets (optional — leave blank to disable)
GOOGLE_SHEET_ID            = os.getenv('GOOGLE_SHEET_ID', '')
GOOGLE_CREDENTIALS_FILE    = os.getenv('GOOGLE_CREDENTIALS_FILE', 'google_credentials.json')

import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
ODDS_API_KEY = os.getenv('ODDS_API_KEY')

PICKS_CHANNEL_ID = int(os.getenv('PICKS_CHANNEL_ID', 0))
RESULTS_CHANNEL_ID = int(os.getenv('RESULTS_CHANNEL_ID', 0))

STARTING_BANKROLL = float(os.getenv('STARTING_BANKROLL', 500))

# Pick settings
MAX_DAILY_PICKS = 5
MIN_EV_THRESHOLD = 0.02  # Minimum 2% edge to post a pick

# Sports to monitor — comment out any not in season
ACTIVE_SPORTS = [
    'americanfootball_nfl',
    'basketball_nba',
    'icehockey_nhl',
    'baseball_mlb',
]

SPORT_DISPLAY = {
    'americanfootball_nfl':   '🏈 NFL',
    'americanfootball_ncaaf': '🏈 NCAAF',
    'basketball_nba':         '🏀 NBA',
    'basketball_ncaab':       '🏀 NCAAB',
    'icehockey_nhl':          '🏒 NHL',
    'baseball_mlb':           '⚾ MLB',
    'soccer_epl':             '⚽ EPL',
    'soccer_mls':             '⚽ MLS',
}

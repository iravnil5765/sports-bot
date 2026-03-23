"""
Google Sheets tracker.
Writes all picks to a Google Sheet with tabs:
  - Dashboard, Game Breakdown, Bankroll

Setup required:
  1. Go to console.cloud.google.com
  2. Create a project → enable Google Sheets API + Google Drive API
  3. Create a Service Account → download JSON credentials
  4. Save the JSON as google_credentials.json in the sports-bot folder
  5. Create a new Google Sheet → share it with the service account email
  6. Copy the Sheet ID from the URL into your .env as GOOGLE_SHEET_ID
"""

import config

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False


SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]


def _get_client():
    if not GSPREAD_AVAILABLE:
        return None
    if not config.GOOGLE_SHEET_ID or not config.GOOGLE_CREDENTIALS_FILE:
        return None
    try:
        import os
        if not os.path.exists(config.GOOGLE_CREDENTIALS_FILE):
            return None
        creds  = Credentials.from_service_account_file(config.GOOGLE_CREDENTIALS_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f'[Sheets] Auth error: {e}')
        return None


def update_sheets(picks_from_db):
    """Write all picks to Google Sheets. Call after any result change."""
    client = _get_client()
    if not client:
        return False

    try:
        sheet = client.open_by_key(config.GOOGLE_SHEET_ID)
        _write_dashboard(sheet, picks_from_db)
        _write_bankroll(sheet, picks_from_db)
        return True
    except Exception as e:
        print(f'[Sheets] Update error: {e}')
        return False


def _get_or_create_tab(sheet, title):
    try:
        return sheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return sheet.add_worksheet(title=title, rows=500, cols=20)


def _write_dashboard(sheet, picks):
    ws = _get_or_create_tab(sheet, 'Dashboard')
    ws.clear()

    headers = [
        'Date', 'Sport', 'Game', 'Bet', 'Confidence',
        'Decimal Odds', 'Units', f'Stake ({config.CURRENCY})',
        f'Est. Return ({config.CURRENCY})', 'True Prob %',
        'Implied Prob %', 'Edge %', 'Result',
        f'P/L ({config.CURRENCY})', 'Betcha Path'
    ]
    ws.append_row(headers)

    unit = config.UNIT_SIZE_NZD

    for p in picks:
        if p['result'] == 'WIN':
            pl = p['units'] * unit * (p['odds'] - 1)
        elif p['result'] == 'LOSS':
            pl = -(p['units'] * unit)
        else:
            pl = 0

        row = [
            p['date'],
            p['sport'],
            f"{p['away_team']} @ {p['home_team']}",
            f"{p['bet_on']} — {p['bet_label']}",
            p.get('confidence', ''),
            round(p['odds'], 2),
            p['units'],
            round(p['units'] * unit, 2),
            round(p['units'] * unit * p['odds'], 2),
            f"{p.get('true_prob', 0)*100:.1f}%",
            f"{(1/p['odds'])*100:.1f}%",
            f"{p.get('ev', 0)*100:.1f}%",
            p['result'],
            round(pl, 2),
            p.get('betcha_path', ''),
        ]
        ws.append_row(row)

    # Summary row
    graded = [p for p in picks if p['result'] in ('WIN', 'LOSS')]
    wins   = sum(1 for p in graded if p['result'] == 'WIN')
    losses = sum(1 for p in graded if p['result'] == 'LOSS')
    wr     = (wins / len(graded) * 100) if graded else 0
    bank   = config.STARTING_BANKROLL
    for p in picks:
        if p['result'] == 'WIN':
            bank += p['units'] * unit * (p['odds'] - 1)
        elif p['result'] == 'LOSS':
            bank -= p['units'] * unit

    ws.append_row([])
    ws.append_row([
        'SUMMARY', '', '', '',
        f'{wins}W — {losses}L ({wr:.1f}% WR)', '', '',
        '', '', '', '', '',
        '', f'Bankroll: ${bank:.2f} {config.CURRENCY}',
        'NZ Gambling Helpline: 0800 654 655'
    ])


def _write_bankroll(sheet, picks):
    ws = _get_or_create_tab(sheet, 'Bankroll')
    ws.clear()
    ws.append_row(['Date', 'Pick', f'Bankroll ({config.CURRENCY})', 'Change', 'Result'])

    unit     = config.UNIT_SIZE_NZD
    bankroll = config.STARTING_BANKROLL
    ws.append_row(['Start', '—', bankroll, 0, '—'])

    for p in picks:
        if p['result'] == 'PENDING':
            continue
        if p['result'] == 'WIN':
            change = p['units'] * unit * (p['odds'] - 1)
        elif p['result'] == 'LOSS':
            change = -(p['units'] * unit)
        else:
            change = 0
        bankroll += change
        label = f"{p['bet_on']} ({p['bet_label']})"
        ws.append_row([p['date'], label, round(bankroll, 2), round(change, 2), p['result']])

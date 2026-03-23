"""
Sports picks Discord bot — NZD / Betcha edition.
Run with: python bot.py
"""

import asyncio
import threading
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

import config
import database
from picks_engine import find_value_picks, to_decimal, determine_result, betcha_path
from odds_api import get_scores
from tracker import generate_tracker, TRACKER_PATH
from sheets import update_sheets
from injuries import clear_cache

# ── Keep-alive web server (stops Railway killing the process) ─────────────────

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, *args):
        pass  # silence request logs

def _start_keepalive():
    port = int(os.getenv('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f'   Keep-alive server on port {port}')

import os

# ── Bot setup ──────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

CONFIDENCE_COLORS = {
    'ELITE':  discord.Color.from_str('#00AA44'),
    'STRONG': discord.Color.from_str('#44BB44'),
    'GOOD':   discord.Color.from_str('#DDAA00'),
    'SKIP':   discord.Color.red(),
}


# ── Events ─────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    database.init_db()
    print(f'✅ Logged in as {bot.user}')
    try:
        synced = await bot.tree.sync()
        print(f'   Synced {len(synced)} slash commands')
    except Exception as e:
        print(f'   Sync failed: {e}')
    if not daily_picks_task.is_running():
        daily_picks_task.start()
    if not check_results_task.is_running():
        check_results_task.start()


# ── Slash commands ─────────────────────────────────────────────────────────────

@bot.tree.command(name='picks', description="Today's picks")
async def cmd_picks(interaction: discord.Interaction):
    picks = database.get_today_picks()
    if not picks:
        await interaction.response.send_message(
            'No picks posted yet today. Check back after 10 AM UTC!', ephemeral=True
        )
        return
    embed = _picks_list_embed(picks, "Today's Picks")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name='record', description='Overall record and bankroll')
async def cmd_record(interaction: discord.Interaction):
    rec    = database.get_record()
    bank   = database.get_bankroll()
    profit = bank - config.STARTING_BANKROLL
    total  = rec['wins'] + rec['losses']
    wr     = (rec['wins'] / total * 100) if total else 0
    daily_pl       = database.get_daily_pl()
    daily_exposure = database.get_daily_exposure()

    embed = discord.Embed(title='📊 All-Time Record', color=discord.Color.gold())
    embed.add_field(name='Record',
                    value=f"**{rec['wins']}W — {rec['losses']}L — {rec['voids']}P**", inline=False)
    embed.add_field(name='Win Rate',    value=f'{wr:.1f}%',                     inline=True)
    embed.add_field(name='Pending',     value=str(rec['pending']),               inline=True)
    embed.add_field(name='Bankroll',    value=f'**${bank:.2f} {config.CURRENCY}**', inline=True)
    p_str = f"+${profit:.2f}" if profit >= 0 else f"-${abs(profit):.2f}"
    embed.add_field(name='All-Time P/L', value=f'**{p_str} {config.CURRENCY}**', inline=True)
    embed.add_field(name="Today's P/L",
                    value=f'{"+" if daily_pl >= 0 else ""}${daily_pl:.2f} {config.CURRENCY}',
                    inline=True)
    embed.add_field(name="Today's Exposure",
                    value=f'${daily_exposure:.2f} / ${config.DAILY_EXPOSURE_LIMIT:.0f} {config.CURRENCY}',
                    inline=True)
    embed.set_footer(text=f'1 unit = ${config.UNIT_SIZE_NZD:.0f} {config.CURRENCY} | Stop-loss: -${config.DAILY_STOP_LOSS:.0f}/day')
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name='history', description='Recent pick history')
async def cmd_history(interaction: discord.Interaction, count: int = 15):
    picks = database.get_recent_picks(min(count, 25))
    if not picks:
        await interaction.response.send_message('No picks yet!', ephemeral=True)
        return
    embed = _picks_list_embed(picks, f'Last {len(picks)} Picks')
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name='pending', description='Ungraded pending picks')
async def cmd_pending(interaction: discord.Interaction):
    picks = database.get_pending_picks()
    if not picks:
        await interaction.response.send_message('No pending picks!', ephemeral=True)
        return
    embed = _picks_list_embed(picks, 'Pending Picks')
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name='export', description='Download Excel tracker')
async def cmd_export(interaction: discord.Interaction):
    await interaction.response.defer()
    path = generate_tracker()
    await interaction.followup.send(
        content='📊 Picks tracker:',
        file=discord.File(path, filename='picks_tracker.xlsx')
    )


@bot.tree.command(name='result', description='[ADMIN] Grade a pick')
async def cmd_result(interaction: discord.Interaction, pick_id: int, result: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('Admins only.', ephemeral=True)
        return
    result = result.upper()
    if result not in ('WIN', 'LOSS', 'VOID'):
        await interaction.response.send_message('Result must be WIN, LOSS, or VOID.', ephemeral=True)
        return
    ok, new_bank = database.grade_pick(pick_id, result)
    if not ok:
        await interaction.response.send_message(
            f'Pick #{pick_id} not found or already graded.', ephemeral=True
        )
        return
    await interaction.response.send_message(
        f'✅ Pick #{pick_id} → **{result}**. Bankroll: **${new_bank:.2f} {config.CURRENCY}**',
        ephemeral=True
    )
    await _announce_result(pick_id, result)


@bot.tree.command(name='refresh', description='[ADMIN] Post new picks now')
async def cmd_refresh(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('Admins only.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    count = await post_picks()
    await interaction.followup.send(f'✅ Posted {count} pick(s).', ephemeral=True)


@bot.tree.command(name='limits', description='Check today\'s exposure and stop-loss status')
async def cmd_limits(interaction: discord.Interaction):
    exposure = database.get_daily_exposure()
    pl       = database.get_daily_pl()
    remaining_exposure = config.DAILY_EXPOSURE_LIMIT - exposure
    stop_loss_remaining = config.DAILY_STOP_LOSS + pl  # how much more we can lose

    color = discord.Color.green()
    if pl <= -config.DAILY_STOP_LOSS:
        color = discord.Color.red()
    elif exposure >= config.DAILY_EXPOSURE_LIMIT * 0.8:
        color = discord.Color.orange()

    embed = discord.Embed(title="📊 Today's Risk Limits", color=color)
    embed.add_field(name='Exposure Used',
                    value=f'${exposure:.2f} / ${config.DAILY_EXPOSURE_LIMIT:.0f} {config.CURRENCY}',
                    inline=True)
    embed.add_field(name='Remaining Budget',
                    value=f'${max(0, remaining_exposure):.2f} {config.CURRENCY}',
                    inline=True)
    embed.add_field(name="Today's P/L",
                    value=f'{"+" if pl >= 0 else ""}${pl:.2f} {config.CURRENCY}',
                    inline=True)

    if pl <= -config.DAILY_STOP_LOSS:
        embed.add_field(name='🚨 STOP-LOSS HIT',
                        value=f'Down ${abs(pl):.2f} today. No more picks.',
                        inline=False)
    else:
        embed.add_field(name='Stop-Loss Buffer',
                        value=f'${stop_loss_remaining:.2f} before stop-loss',
                        inline=True)

    embed.set_footer(text='NZ Gambling Helpline: 0800 654 655')
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name='help', description='Show all commands')
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(title='📖 Commands', color=discord.Color.blurple())
    embed.add_field(name='/picks',              value="Today's picks",              inline=False)
    embed.add_field(name='/record',             value='Record + bankroll',           inline=False)
    embed.add_field(name='/history [count]',    value='Recent history',              inline=False)
    embed.add_field(name='/pending',            value='Ungraded picks',              inline=False)
    embed.add_field(name='/limits',             value="Today's exposure & stop-loss",inline=False)
    embed.add_field(name='/export',             value='Download Excel tracker',      inline=False)
    embed.add_field(name='── Admin ──',         value='\u200b',                     inline=False)
    embed.add_field(name='/result <id> <W/L/V>',value='Grade a pick',               inline=False)
    embed.add_field(name='/refresh',            value='Force-fetch picks now',       inline=False)
    embed.set_footer(text='NZ Gambling Helpline: 0800 654 655')
    await interaction.response.send_message(embed=embed)


# ── Scheduled tasks ────────────────────────────────────────────────────────────

@tasks.loop(minutes=140)
async def daily_picks_task():
    """Check for new value picks every 2h20m starting at 10:40 AM UTC (10:40 PM NZT)."""
    clear_cache()
    await post_picks()

@daily_picks_task.before_loop
async def before_daily_picks():
    """Wait until 10:40 AM UTC before starting the loop."""
    await bot.wait_until_ready()
    now    = datetime.now(timezone.utc)
    target = now.replace(hour=10, minute=40, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    wait_seconds = (target - now).total_seconds()
    print(f'⏳ First pick check in {wait_seconds/3600:.1f} hours (10:40 AM UTC)')
    await asyncio.sleep(wait_seconds)


@tasks.loop(hours=1)
async def check_results_task():
    pending = database.get_pending_picks()
    if not pending:
        return
    now = datetime.now(timezone.utc)
    for pick in pending:
        try:
            ct = datetime.fromisoformat(pick['commence_time'])
            if ct.tzinfo is None:
                ct = ct.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if now < ct + timedelta(hours=3):
            continue
        sport_scores = await get_scores(pick['sport'])
        for game in sport_scores:
            if game.get('id') != pick['game_id']:
                continue
            result = determine_result(pick, game)
            if result:
                database.grade_pick(pick['id'], result)
                await _announce_result(pick['id'], result)
            break
        await asyncio.sleep(0.2)


# ── Core helpers ───────────────────────────────────────────────────────────────

async def post_picks():
    ch = bot.get_channel(config.PICKS_CHANNEL_ID)
    if not ch:
        print(f'[bot] picks channel {config.PICKS_CHANNEL_ID} not found')
        return 0

    # Send scanning message
    scan_msg = await ch.send(embed=discord.Embed(
        title='🔍 Scanning NBA, EPL, NHL...',
        description='Checking odds across all bookmakers for value bets.',
        color=discord.Color.blurple()
    ))

    picks     = await find_value_picks()
    today_str = datetime.now().strftime('%A, %B %d %Y')

    # Filter out games already posted
    existing        = database.get_all_picks()
    posted_game_ids = {p['game_id'] for p in existing}
    filtered        = [p for p in picks if p['game_id'] not in posted_game_ids]

    if not filtered:
        await scan_msg.edit(embed=discord.Embed(
            title='😴 No value found this check',
            description='No new picks right now. Next check in 2h 20m.',
            color=discord.Color.greyple()
        ))
        return 0

    await scan_msg.edit(embed=discord.Embed(
        title=f'✅ Found {len(filtered)} new pick(s) — posting now...',
        color=discord.Color.green()
    ))

    total_stake = sum(p['stake_nzd'] for p in filtered)
    header = discord.Embed(
        title=f'🎯 Picks — {today_str}',
        description=(
            f'**{len(filtered)}** value bet(s) found.\n'
            f'Total exposure: **${total_stake:.2f} {config.CURRENCY}** / '
            f'${config.DAILY_EXPOSURE_LIMIT:.0f} limit\n'
            f'1 unit = **${config.UNIT_SIZE_NZD:.0f} {config.CURRENCY}**'
        ),
        color=discord.Color.green()
    )
    header.set_footer(text='Picks are based on mathematical edge vs. consensus odds. NZ Gambling Helpline: 0800 654 655')
    await ch.send(embed=header)

    count = 0
    for pick in filtered:
        bpath   = betcha_path(pick)
        pick_id = database.add_pick(
            date          = datetime.now().strftime('%Y-%m-%d'),
            sport         = pick['sport'],
            home_team     = pick['home_team'],
            away_team     = pick['away_team'],
            bet_type      = pick['bet_type'],
            bet_on        = pick['bet_on'],
            bet_label     = pick['bet_label'],
            odds          = pick['odds'],
            units         = pick['units'],
            stake_nzd     = pick['stake_nzd'],
            ev            = pick['ev'],
            true_prob     = pick['true_prob'],
            confidence    = pick['confidence'],
            injury_alert  = pick.get('injury_alert', ''),
            betcha_path   = bpath,
            game_id       = pick['game_id'],
            commence_time = pick['commence_time'].isoformat(),
        )
        embed = _single_pick_embed(pick, pick_id, bpath)
        msg   = await ch.send(embed=embed)
        database.set_message_id(pick_id, msg.id)
        count += 1
        await asyncio.sleep(0.5)

    _refresh_trackers()
    return count


async def _announce_result(pick_id, result):
    ch_id = config.RESULTS_CHANNEL_ID or config.PICKS_CHANNEL_ID
    ch    = bot.get_channel(ch_id)
    if not ch:
        return
    pick   = database.get_pick(pick_id)
    if not pick:
        return
    bank   = database.get_bankroll()
    profit = bank - config.STARTING_BANKROLL
    unit_v = config.UNIT_SIZE_NZD

    colors = {'WIN': discord.Color.green(), 'LOSS': discord.Color.red(), 'VOID': discord.Color.greyple()}
    icons  = {'WIN': '✅', 'LOSS': '❌', 'VOID': '↩️'}

    embed = discord.Embed(
        title=f"{icons[result]} Pick #{pick_id} — {result}",
        color=colors[result]
    )
    embed.add_field(name='Game',  value=f"{pick['away_team']} @ {pick['home_team']}", inline=False)
    embed.add_field(name='Bet',   value=f"{pick['bet_on']} ({pick['bet_label']})",    inline=True)
    embed.add_field(name='Odds',  value=str(pick['odds']),                             inline=True)
    embed.add_field(name='Units', value=f"{pick['units']}u",                           inline=True)

    if result == 'WIN':
        chg = pick['units'] * unit_v * (pick['odds'] - 1)
        embed.add_field(name='Profit', value=f'+${chg:.2f} {config.CURRENCY}', inline=True)
    elif result == 'LOSS':
        chg = pick['units'] * unit_v
        embed.add_field(name='Loss',   value=f'-${chg:.2f} {config.CURRENCY}', inline=True)

    embed.add_field(name='Bankroll',     value=f'**${bank:.2f} {config.CURRENCY}**', inline=True)
    p_str = f"+${profit:.2f}" if profit >= 0 else f"-${abs(profit):.2f}"
    embed.add_field(name='All-Time P/L', value=f'{p_str} {config.CURRENCY}',          inline=True)
    embed.set_footer(text='NZ Gambling Helpline: 0800 654 655')
    await ch.send(embed=embed)

    _refresh_trackers()


def _refresh_trackers():
    try:
        generate_tracker()
    except Exception:
        pass
    try:
        all_picks = database.get_all_picks()
        update_sheets(all_picks)
    except Exception:
        pass


def _single_pick_embed(pick, pick_id, bpath):
    sport_name = config.SPORT_DISPLAY.get(pick['sport'], pick['sport'])
    confidence = pick['confidence']
    conf_info  = config.CONFIDENCE.get(confidence, ('', confidence, '888888'))
    color      = CONFIDENCE_COLORS.get(confidence, discord.Color.blue())
    ct         = pick['commence_time']
    time_str   = ct.strftime('%b %d, %I:%M %p UTC') if hasattr(ct, 'strftime') else str(ct)

    embed = discord.Embed(
        title=f'{conf_info[1]} — Pick #{pick_id} | {sport_name}',
        color=color
    )
    embed.add_field(name='Game',
                    value=f"**{pick['away_team']}** @ **{pick['home_team']}**", inline=False)
    embed.add_field(name='Bet',
                    value=f"**{pick['bet_on']}** — {pick['bet_label']}",        inline=True)
    embed.add_field(name='Decimal Odds',
                    value=f"**{pick['odds']:.2f}**",                             inline=True)
    embed.add_field(name='Units / Stake',
                    value=f"**{pick['units']}u** (${pick['stake_nzd']:.2f} {config.CURRENCY})", inline=True)
    embed.add_field(name='Est. Return',
                    value=f"${pick['return_nzd']:.2f} {config.CURRENCY}",        inline=True)
    embed.add_field(name='My Win Prob',
                    value=f"{pick['true_prob']*100:.1f}%",                        inline=True)
    embed.add_field(name='Implied Prob',
                    value=f"{pick['implied_prob']*100:.1f}%",                     inline=True)
    embed.add_field(name='Edge',
                    value=f"+{pick['ev']*100:.1f}%",                              inline=True)
    embed.add_field(name='Game Time',   value=time_str,                           inline=True)
    embed.add_field(name='🔍 Find on Betcha', value=f'`{bpath}`',                inline=False)

    if pick.get('injury_alert'):
        embed.add_field(name='⚠️ Injury Alert', value=pick['injury_alert'],      inline=False)

    embed.set_footer(text=f'ID #{pick_id} | result pending | NZ Gambling Helpline: 0800 654 655')
    return embed


def _picks_list_embed(picks, title):
    bank   = database.get_bankroll()
    embed  = discord.Embed(title=f'📋 {title}', color=discord.Color.blue())
    icons  = {'WIN': '✅', 'LOSS': '❌', 'VOID': '↩️', 'PENDING': '⏳'}
    conf_icons = {'ELITE': '🟢', 'STRONG': '🟢', 'GOOD': '🟡', 'SKIP': '🔴', '': '⚪'}

    for p in picks:
        icon  = icons.get(p['result'], '⏳')
        ci    = conf_icons.get(p.get('confidence', ''), '⚪')
        name  = f"{icon} #{p['id']} — {p['away_team']} @ {p['home_team']}"
        value = (f"{ci} {p['bet_on']} | **{p['odds']:.2f}** | "
                 f"{p['units']}u (${p.get('stake_nzd', 0):.2f}) | **{p['result']}**")
        embed.add_field(name=name, value=value, inline=False)

    embed.set_footer(text=f'Bankroll: ${bank:.2f} {config.CURRENCY} | NZ Gambling Helpline: 0800 654 655')
    return embed


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    database.init_db()
    _start_keepalive()
    bot.run(config.DISCORD_TOKEN)

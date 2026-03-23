"""
Sports picks Discord bot.
Run with: python bot.py
"""

import asyncio
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

import config
import database
from picks_engine import find_value_picks, to_american, determine_result
from odds_api import get_scores
from tracker import generate_tracker, TRACKER_PATH

# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)


# ── Events ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    database.init_db()
    print(f'✅ Logged in as {bot.user}')

    try:
        synced = await bot.tree.sync()
        print(f'   Synced {len(synced)} slash commands')
    except Exception as e:
        print(f'   Slash command sync failed: {e}')

    if not daily_picks_task.is_running():
        daily_picks_task.start()
    if not check_results_task.is_running():
        check_results_task.start()


# ── Slash commands ─────────────────────────────────────────────────────────────

@bot.tree.command(name='picks', description="Show today's picks")
async def cmd_picks(interaction: discord.Interaction):
    picks = database.get_today_picks()
    if not picks:
        await interaction.response.send_message(
            "No picks posted yet today. Check back after 10 AM UTC!", ephemeral=True
        )
        return
    embed = _picks_list_embed(picks, "Today's Picks")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name='record', description='Overall record and bankroll')
async def cmd_record(interaction: discord.Interaction):
    rec  = database.get_record()
    bank = database.get_bankroll()
    profit = bank - config.STARTING_BANKROLL
    total  = rec['wins'] + rec['losses']
    wr     = (rec['wins'] / total * 100) if total else 0

    embed = discord.Embed(title='📊 All-Time Record', color=discord.Color.gold())
    embed.add_field(name='Record',
                    value=f"**{rec['wins']}W — {rec['losses']}L — {rec['voids']}P**",
                    inline=False)
    embed.add_field(name='Win Rate',  value=f'{wr:.1f}%',       inline=True)
    embed.add_field(name='Pending',   value=f"{rec['pending']}", inline=True)
    embed.add_field(name='Bankroll',  value=f'**${bank:,.2f}**', inline=True)
    p_str = f"+${profit:,.2f}" if profit >= 0 else f"-${abs(profit):,.2f}"
    embed.add_field(name='Profit/Loss',
                    value=f'**{p_str}** from ${config.STARTING_BANKROLL:,.0f}',
                    inline=True)
    embed.set_footer(text=f"1 unit = ${config.STARTING_BANKROLL * 0.01:.0f}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name='history', description='Recent pick history')
async def cmd_history(interaction: discord.Interaction, count: int = 15):
    picks = database.get_recent_picks(min(count, 25))
    if not picks:
        await interaction.response.send_message('No picks yet!', ephemeral=True)
        return
    embed = _picks_list_embed(picks, f'Last {len(picks)} Picks')
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name='pending', description='Show ungraded pending picks')
async def cmd_pending(interaction: discord.Interaction):
    picks = database.get_pending_picks()
    if not picks:
        await interaction.response.send_message('No pending picks!', ephemeral=True)
        return
    embed = _picks_list_embed(picks, 'Pending Picks')
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name='result', description='[ADMIN] Grade a pick')
async def cmd_result(interaction: discord.Interaction, pick_id: int, result: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('Admins only.', ephemeral=True)
        return

    result = result.upper()
    if result not in ('WIN', 'LOSS', 'VOID'):
        await interaction.response.send_message(
            'Result must be WIN, LOSS, or VOID.', ephemeral=True
        )
        return

    ok, new_bank = database.grade_pick(pick_id, result)
    if not ok:
        await interaction.response.send_message(
            f'Pick #{pick_id} not found or already graded.', ephemeral=True
        )
        return

    await interaction.response.send_message(
        f'✅ Pick #{pick_id} → **{result}**. Bankroll: **${new_bank:,.2f}**',
        ephemeral=True
    )
    await _announce_result(pick_id, result)


@bot.tree.command(name='refresh', description='[ADMIN] Post new picks right now')
async def cmd_refresh(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('Admins only.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    count = await post_picks()
    await interaction.followup.send(f'✅ Posted {count} pick(s).', ephemeral=True)


@bot.tree.command(name='export', description='Download the full picks tracker as an Excel file')
async def cmd_export(interaction: discord.Interaction):
    await interaction.response.defer()
    path = generate_tracker()
    await interaction.followup.send(
        content='📊 Here\'s the full picks tracker:',
        file=discord.File(path, filename='picks_tracker.xlsx')
    )


@bot.tree.command(name='help', description='Show all commands')
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(title='📖 Commands', color=discord.Color.blurple())
    embed.add_field(name='/picks',              value="Today's picks",             inline=False)
    embed.add_field(name='/record',             value='Win/loss record + bankroll', inline=False)
    embed.add_field(name='/history [count]',    value='Recent pick history',        inline=False)
    embed.add_field(name='/pending',            value='Ungraded picks',             inline=False)
    embed.add_field(name='/export',             value='Download Excel tracker',     inline=False)
    embed.add_field(name='── Admin ──',         value='\u200b',                    inline=False)
    embed.add_field(name='/result <id> <W/L/V>',value='Grade a pick',              inline=False)
    embed.add_field(name='/refresh',            value='Force-fetch new picks now',  inline=False)
    await interaction.response.send_message(embed=embed)


# ── Scheduled tasks ────────────────────────────────────────────────────────────

@tasks.loop(hours=24)
async def daily_picks_task():
    """Sleep until 10:00 AM UTC, then post picks."""
    now    = datetime.now(timezone.utc)
    target = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    await asyncio.sleep((target - now).total_seconds())
    await post_picks()


@tasks.loop(hours=1)
async def check_results_task():
    """Every hour, check if any pending picks have finished."""
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

        # Only check games that started at least 3 hours ago
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
    """Fetch value picks and post them to PICKS_CHANNEL_ID. Returns count posted."""
    ch = bot.get_channel(config.PICKS_CHANNEL_ID)
    if not ch:
        print(f'[bot] picks channel {config.PICKS_CHANNEL_ID} not found')
        return 0

    picks = await find_value_picks()

    unit_dollar = config.STARTING_BANKROLL * 0.01
    today_str   = datetime.now().strftime('%A, %B %d %Y')

    if not picks:
        await ch.send(embed=discord.Embed(
            title='📋 No picks today',
            description='No edge found in today\'s lines. Protecting the bankroll 💰',
            color=discord.Color.orange()
        ))
        return 0

    header = discord.Embed(
        title=f'🎯 Picks — {today_str}',
        description=(
            f'Found **{len(picks)}** value bet(s).\n'
            f'1 unit = **${unit_dollar:.0f}** '
            f'(1% of ${config.STARTING_BANKROLL:,.0f} bankroll)'
        ),
        color=discord.Color.green()
    )
    header.set_footer(text='Picks are based on mathematical edge vs. consensus odds. Bet responsibly.')
    await ch.send(embed=header)

    count = 0
    for pick in picks:
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
            ev            = pick['ev'],
            game_id       = pick['game_id'],
            commence_time = pick['commence_time'].isoformat(),
        )
        embed = _single_pick_embed(pick, pick_id)
        msg   = await ch.send(embed=embed)
        database.set_message_id(pick_id, msg.id)
        count += 1
        await asyncio.sleep(0.5)

    return count


async def _announce_result(pick_id, result):
    """Post a result update to the results (or picks) channel."""
    ch_id = config.RESULTS_CHANNEL_ID or config.PICKS_CHANNEL_ID
    ch    = bot.get_channel(ch_id)
    if not ch:
        return

    pick = database.get_pick(pick_id)
    if not pick:
        return

    bank   = database.get_bankroll()
    profit = bank - config.STARTING_BANKROLL
    unit_v = config.STARTING_BANKROLL * 0.01

    colors = {'WIN': discord.Color.green(), 'LOSS': discord.Color.red(),   'VOID': discord.Color.greyple()}
    icons  = {'WIN': '✅',                  'LOSS': '❌',                   'VOID': '↩️'}

    embed = discord.Embed(
        title=f"{icons[result]} Pick #{pick_id} — {result}",
        color=colors[result]
    )
    embed.add_field(name='Game', value=f"{pick['away_team']} @ {pick['home_team']}", inline=False)
    embed.add_field(name='Bet',  value=f"{pick['bet_on']} ({pick['bet_label']})",   inline=True)
    embed.add_field(name='Odds', value=to_american(pick['odds']),                    inline=True)
    embed.add_field(name='Units', value=f"{pick['units']}u",                         inline=True)

    if result == 'WIN':
        chg = pick['units'] * unit_v * (pick['odds'] - 1)
        embed.add_field(name='Profit', value=f'+${chg:.2f}', inline=True)
    elif result == 'LOSS':
        chg = pick['units'] * unit_v
        embed.add_field(name='Loss',   value=f'-${chg:.2f}', inline=True)

    embed.add_field(name='Bankroll', value=f'**${bank:,.2f}**', inline=True)
    p_str = f"+${profit:,.2f}" if profit >= 0 else f"-${abs(profit):,.2f}"
    embed.add_field(name='All-Time P/L', value=p_str, inline=True)
    await ch.send(embed=embed)

    # Silently regenerate the tracker so /export is always up to date
    try:
        generate_tracker()
    except Exception:
        pass


def _single_pick_embed(pick, pick_id):
    sport_name = config.SPORT_DISPLAY.get(pick['sport'], pick['sport'])
    unit_v     = config.STARTING_BANKROLL * 0.01
    ct         = pick['commence_time']
    time_str   = ct.strftime('%b %d, %I:%M %p UTC') if hasattr(ct, 'strftime') else str(ct)

    embed = discord.Embed(
        title=f'Pick #{pick_id} — {sport_name}',
        color=discord.Color.blue()
    )
    embed.add_field(name='Game',  value=f"**{pick['away_team']}** @ **{pick['home_team']}**", inline=False)
    embed.add_field(name='Bet',   value=f"**{pick['bet_on']}** — {pick['bet_label']}",        inline=True)
    embed.add_field(name='Odds',  value=f"**{to_american(pick['odds'])}** ({pick['odds']:.2f})", inline=True)
    embed.add_field(name='Units', value=f"**{pick['units']}u** (${pick['units'] * unit_v:.0f})", inline=True)
    embed.add_field(name='Edge',  value=f"+{pick['ev']*100:.1f}%",                            inline=True)
    embed.add_field(name='True Prob', value=f"{pick['true_prob']*100:.1f}%",                  inline=True)
    embed.add_field(name='Game Time', value=time_str,                                          inline=True)
    embed.set_footer(text=f'ID #{pick_id} | result pending')
    return embed


def _picks_list_embed(picks, title):
    bank  = database.get_bankroll()
    embed = discord.Embed(title=f'📋 {title}', color=discord.Color.blue())
    icons = {'WIN': '✅', 'LOSS': '❌', 'VOID': '↩️', 'PENDING': '⏳'}

    for p in picks:
        icon  = icons.get(p['result'], '⏳')
        name  = f"{icon} #{p['id']} — {p['away_team']} @ {p['home_team']}"
        value = f"{p['bet_on']} | {to_american(p['odds'])} | {p['units']}u | **{p['result']}**"
        embed.add_field(name=name, value=value, inline=False)

    embed.set_footer(text=f'Bankroll: ${bank:,.2f}')
    return embed


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    database.init_db()
    bot.run(config.DISCORD_TOKEN)

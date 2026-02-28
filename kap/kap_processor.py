"""
KAP (Keeper Assignment Period) Processor
Handles keeper selection submissions, contract updates, and WizBucks transactions
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel

from data_lock import DATA_LOCK

# Keeper salary constants
KEEPER_SALARIES = {
    'TC-R': 5,
    'TC-BC-1': 5,
    'TC-BC-2': 5,
    'TC-1': 15,
    'TC-2': 25,
    'VC-1': 35,
    'VC-2': 55,
    'FC-1': 85,
    'FC-2': 125,
    'FC-2+': 125
}

# IL Tag discounts
IL_DISCOUNTS = {
    'TC': 10,
    'VC': 15,
    'FC': 35
}

# Contract advancement
# Progression: TC-R → TC-1 → TC-2 → VC-1 → VC-2 → FC-1 → FC-2+
# Blue Chip exception: TC-BC-1 → TC-BC-2 → TC-1 (then follows normal progression)
CONTRACT_ADVANCEMENT = {
    'TC-R': 'TC-1',
    'TC-BC-1': 'TC-BC-2',  # Blue Chip year 1
    'TC-BC-2': 'TC-1',     # Blue Chip year 2 → enters normal progression
    'TC-1': 'TC-2',
    'TC-2': 'VC-1',
    'VC-1': 'VC-2',
    'VC-2': 'FC-1',
    'FC-1': 'FC-2+',
    'FC-2+': 'FC-2+'       # Terminal tier
}

# Mapping between years_simple values and CONTRACT_ADVANCEMENT keys
_YEARS_SIMPLE_TO_KEY = {
    'TC R': 'TC-R',
    'TC BC-1': 'TC-BC-1',
    'TC BC-2': 'TC-BC-2',
    'TC 1': 'TC-1',
    'TC 2': 'TC-2',
    'VC 1': 'VC-1',
    'VC 2': 'VC-2',
    'FC 1': 'FC-1',
    'FC 2': 'FC-2+',
}

# Reverse: advancement key → (years_simple, status)
_KEY_TO_FIELDS = {
    'TC-R':    ('TC R',    '[6] TCR'),
    'TC-BC-1': ('TC BC-1', '[6] TCBC1'),
    'TC-BC-2': ('TC BC-2', '[5] TCBC2'),
    'TC-1':    ('TC 1',    '[5] TC1'),
    'TC-2':    ('TC 2',    '[4] TC2'),
    'VC-1':    ('VC 1',    '[3] VC1'),
    'VC-2':    ('VC 2',    '[2] VC2'),
    'FC-1':    ('FC 1',    '[1] FC1'),
    'FC-2+':   ('FC 2',    '[0] FC2'),
}


class KeeperPlayer(BaseModel):
    """Keeper player with contract info"""
    upid: str
    name: str
    contract: str
    has_il_tag: bool = False
    has_rat: bool = False


class KAPSubmission(BaseModel):
    """KAP submission payload"""
    team: str
    season: int = 2026
    keepers: List[KeeperPlayer]
    il_tags: Dict[str, Optional[str]] = {}  # tier -> upid
    rat_applications: List[str] = []  # list of upids
    buyins_purchased: List[int] = []  # rounds purchased (from separate buyin endpoint)
    taxable_spend: int
    submitted_by: str


class KAPResult(BaseModel):
    """KAP submission result"""
    season: int
    team: str
    timestamp: str
    keepers_selected: int
    keeper_salary_cost: int
    rat_cost: int
    buyin_cost: int
    total_taxable_spend: int
    wb_spent: int
    wb_remaining: int
    draft_picks_taxed: List[int]


def _load_json(path: str) -> any:
    """Load JSON file"""
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        return json.load(f)


def _save_json(path: str, data: any):
    """Save JSON file"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def _commit_and_push_kap_files(team: str, season: int, keeper_count: int, files: list):
    """Commit and push KAP submission files to GitHub"""
    import subprocess
    
    repo_root = os.getenv('REPO_ROOT', os.getcwd())
    
    try:
        # Add files
        subprocess.run(
            ['git', 'add'] + files,
            check=True,
            cwd=repo_root,
            capture_output=True,
            text=True
        )
        
        # Commit
        commit_message = f'KAP {season}: {team} submitted {keeper_count} keepers'
        subprocess.run(
            ['git', 'commit', '-m', commit_message],
            check=True,
            cwd=repo_root,
            capture_output=True,
            text=True
        )
        
        # Push
        token = os.getenv('GITHUB_TOKEN')
        if token:
            repo = os.getenv('GITHUB_REPO', 'zpressley/FBPTradeBot')
            username = os.getenv('GITHUB_USER', 'x-access-token')
            remote_url = f'https://{username}:{token}@github.com/{repo}.git'
            push_cmd = ['git', 'push', remote_url, 'HEAD:main']
        else:
            push_cmd = ['git', 'push']
        
        subprocess.run(
            push_cmd,
            check=True,
            cwd=repo_root,
            capture_output=True,
            text=True
        )
        
        print(f'✅ KAP files committed and pushed to GitHub for {team}')
        
    except subprocess.CalledProcessError as exc:
        print(f'⚠️ KAP git commit/push failed with code {exc.returncode}')
        print(f'   stdout: {exc.stdout}')
        print(f'   stderr: {exc.stderr}')
    except Exception as exc:
        print(f'⚠️ KAP git commit/push error: {exc}')


def calculate_keeper_cost(player: KeeperPlayer) -> int:
    """Calculate final keeper cost after IL tag"""
    base_cost = KEEPER_SALARIES.get(player.contract, 0)
    
    if player.has_il_tag:
        # Determine tier from contract
        if player.contract.startswith('TC'):
            discount = IL_DISCOUNTS['TC']
        elif player.contract.startswith('VC'):
            discount = IL_DISCOUNTS['VC']
        elif player.contract.startswith('FC'):
            discount = IL_DISCOUNTS['FC']
        else:
            discount = 0
        return max(0, base_cost - discount)
    
    return base_cost


def get_tax_bracket(taxable_spend: int) -> Dict:
    """Get tax bracket for spend amount"""
    brackets = [
        {'min': 421, 'max': 435, 'rounds': [4, 5, 6, 7, 8]},
        {'min': 401, 'max': 420, 'rounds': [5, 6, 7]},
        {'min': 376, 'max': 400, 'rounds': [6, 7, 8]},
        {'min': 351, 'max': 375, 'rounds': [7, 8, 9]},
        {'min': 326, 'max': 350, 'rounds': [8, 9, 10]},
        {'min': 0, 'max': 325, 'rounds': []}
    ]
    
    for bracket in brackets:
        if taxable_spend >= bracket['min'] and taxable_spend <= bracket['max']:
            return bracket
    
    return {'min': 0, 'max': 325, 'rounds': []}


def process_kap_submission(submission: KAPSubmission, test_mode: bool = False) -> KAPResult:
    """
    Process KAP submission:
    1. Update player contracts in combined_players.json
    2. Create player_log entries for each keeper
    3. Update draft_order_2026.json with taxed picks
    4. Deduct WizBucks from wallet
    5. Log transaction to wizbucks_transactions.json
    """
    
    season = submission.season
    team = submission.team
    now = datetime.utcnow().isoformat()
    
    # File paths
    combined_path = 'data/combined_players.json'
    player_log_path = 'data/player_log.json'
    draft_order_path = 'data/draft_order_2026.json'
    wizbucks_path = 'data/wizbucks.json'
    transactions_path = 'data/wizbucks_transactions.json'
    submissions_path = f'data/kap_submissions{"_test" if test_mode else ""}.json'
    
    # Load data — hold DATA_LOCK for the full load-modify-save cycle
    _data_lock_acquired = DATA_LOCK.acquire()

    combined_players = _load_json(combined_path) or []
    player_log = _load_json(player_log_path) or []
    draft_order = _load_json(draft_order_path) or []
    wizbucks = _load_json(wizbucks_path) or {}
    transactions = _load_json(transactions_path) or []
    submissions = _load_json(submissions_path) or {}
    
    # Get team's full name for wizbucks
    managers = _load_json('config/managers.json') or {}
    team_name = managers.get('teams', {}).get(team, {}).get('name', team)
    
    # Calculate costs
    keeper_salary_cost = sum(calculate_keeper_cost(k) for k in submission.keepers)
    rat_cost = len(submission.rat_applications) * 75
    
    # Buy-ins already purchased via separate endpoint, just get total
    buyin_costs = {1: 55, 2: 35, 3: 10}
    buyin_cost = sum(buyin_costs[r] for r in submission.buyins_purchased)
    
    total_taxable_spend = keeper_salary_cost + buyin_cost
    total_spend = total_taxable_spend + rat_cost
    
    # Get tax bracket
    tax_bracket = get_tax_bracket(total_taxable_spend)
    taxed_rounds = tax_bracket['rounds']
    
    # Update player contracts in combined_players
    keeper_upids = set(k.upid for k in submission.keepers)
    
    for player in combined_players:
        player_upid = str(player.get('upid', ''))
        
        # Check if this player belongs to the submitting team
        is_team_player = (
            player.get('manager') == team_name
            or str(player.get('FBP_Team') or '').upper() == team.upper()
        )
        
        if player_upid in keeper_upids:
            # Find keeper info
            keeper = next((k for k in submission.keepers if k.upid == player_upid), None)
            if not keeper:
                continue
            
            # years_simple holds the contract tier (e.g. "TC R", "TC 1")
            old_years_simple = (player.get('years_simple') or '').strip()
            contract_key = _YEARS_SIMPLE_TO_KEY.get(old_years_simple, '')
            new_key = CONTRACT_ADVANCEMENT.get(contract_key, contract_key)
            
            # Write back to both years_simple and status
            if new_key and new_key in _KEY_TO_FIELDS:
                new_ys, new_status = _KEY_TO_FIELDS[new_key]
                player['years_simple'] = new_ys
                player['status'] = new_status
            
            # Log to player_log
            player_log.append({
                'timestamp': now,
                'team': team,
                'player': {
                    'upid': player_upid,
                    'name': player.get('name', ''),
                    'mlb_team': player.get('team', '')
                },
                'action': 'keeper_selection',
                'details': {
                    'season': season,
                    'old_contract': contract_key,
                    'new_contract': new_key,
                    'salary': calculate_keeper_cost(keeper),
                    'has_il_tag': keeper.has_il_tag,
                    'has_rat': keeper.has_rat
                }
            })
        elif is_team_player:
            # Release non-kept players — clear ownership so they go to the draft
            old_years_simple = (player.get('years_simple') or '').strip()
            contract_key = _YEARS_SIMPLE_TO_KEY.get(old_years_simple, old_years_simple)
            player_log.append({
                'timestamp': now,
                'team': team,
                'player': {
                    'upid': player_upid,
                    'name': player.get('name', ''),
                    'mlb_team': player.get('team', '')
                },
                'action': 'kap_release',
                'details': {
                    'season': season,
                    'old_contract': contract_key,
                }
            })
            player['manager'] = None
            player['FBP_Team'] = None
            player['contract_type'] = None
    
    # Update draft picks — mark taxed rounds + keeper-filled slots
    ROSTER_SIZE = 26
    picks_for_draft = ROSTER_SIZE - len(submission.keepers)

    # Get all keeper-draft picks owned by this team, excluding unpurchased buy-ins
    team_picks = [
        p for p in draft_order
        if p.get('draft') == 'keeper'
        and p.get('current_owner') == team
        and not (p.get('buyin_required') and not p.get('buyin_purchased'))
    ]
    team_picks.sort(key=lambda p: (p.get('round', 0), p.get('pick', 0)))

    # First pass: mark taxed picks
    for pick in team_picks:
        if pick.get('round') in taxed_rounds:
            pick['taxed_out'] = True

    # Second pass: walk from top, keep picks_for_draft non-taxed picks for drafting,
    # mark the rest as result: "keeper" (filled by keepers from the bottom up)
    usable_count = 0
    for pick in team_picks:
        if pick.get('taxed_out'):
            continue  # taxed picks don't count toward either bucket
        if usable_count < picks_for_draft:
            usable_count += 1  # this is a draft pick — leave as-is
        else:
            pick['result'] = 'keeper'  # filled by a keeper
    
    # Deduct WizBucks
    if not test_mode and total_spend > 0:
        from wb_ledger import append_transaction

        entry = append_transaction(
            team=team,
            amount=-total_spend,
            transaction_type="KAP_submission",
            description=f"{season} KAP: {len(submission.keepers)} keepers selected",
            metadata={
                "season": season,
                "keeper_count": len(submission.keepers),
                "keeper_salary": keeper_salary_cost,
                "rat_cost": rat_cost,
                "buyin_cost": buyin_cost,
                "taxable_spend": total_taxable_spend,
                "taxed_rounds": taxed_rounds,
                "submitted_by": submission.submitted_by,
            },
        )
        current_balance = entry["balance_before"]
        new_balance = entry["balance_after"]
    else:
        current_balance = wizbucks.get(team_name, 0)
        new_balance = current_balance - total_spend
        wizbucks[team_name] = new_balance

        transactions.append({
            'id': f'kap_{team}_{now}',
            'timestamp': now,
            'team': team,
            'team_name': team_name,
            'amount': -total_spend,
            'balance_before': current_balance,
            'balance_after': new_balance,
            'transaction_type': 'KAP_submission',
            'description': f'{season} KAP: {len(submission.keepers)} keepers selected',
            'metadata': {
                'season': season,
                'keeper_count': len(submission.keepers),
                'keeper_salary': keeper_salary_cost,
                'rat_cost': rat_cost,
                'buyin_cost': buyin_cost,
                'taxable_spend': total_taxable_spend,
                'taxed_rounds': taxed_rounds,
                'submitted_by': submission.submitted_by,
            },
        })
    
    # Save submission metadata
    submissions[team] = {
        'season': season,
        'team': team,
        'timestamp': now,
        'keeper_count': len(submission.keepers),
        'keepers': [k.dict() for k in submission.keepers],
        'taxable_spend': total_taxable_spend,
        'tax_bracket': tax_bracket,
        'submitted_by': submission.submitted_by
    }
    
    # Save data files.
    # In live mode, wb_ledger.append_transaction already wrote wizbucks.json
    # and wizbucks_transactions.json — do NOT overwrite them with stale
    # in-memory copies.  In test mode we manage those files ourselves.
    if not test_mode:
        _save_json(combined_path, combined_players)
        _save_json(player_log_path, player_log)
        _save_json(draft_order_path, draft_order)
        _save_json(submissions_path, submissions)
        # wizbucks.json + transactions handled by wb_ledger — skip
    else:
        _save_json(combined_path, combined_players)
        _save_json(player_log_path, player_log)
        _save_json(draft_order_path, draft_order)
        _save_json(wizbucks_path, wizbucks)
        _save_json(transactions_path, transactions)
        _save_json(submissions_path, submissions)

    # Release the data lock now that all file mutations are complete.
    if _data_lock_acquired:
        DATA_LOCK.release()

    return KAPResult(
        season=season,
        team=team,
        timestamp=now,
        keepers_selected=len(submission.keepers),
        keeper_salary_cost=keeper_salary_cost,
        rat_cost=rat_cost,
        buyin_cost=buyin_cost,
        total_taxable_spend=total_taxable_spend,
        wb_spent=total_spend,
        wb_remaining=new_balance,
        draft_picks_taxed=taxed_rounds
    )


async def announce_kap_submission_to_discord(result: KAPResult, bot) -> None:
    """Send KAP submission to Discord transactions channel"""
    
    import os
    import discord
    
    # Use same transactions channel as PAD (1089979265619083444)
    channel_id = 1089979265619083444
    
    channel = bot.get_channel(channel_id)
    if channel is None:
        print(f"⚠️ KAP announce: channel {channel_id} not found")
        return
    
    title = f"{result.team} – KAP Submission ({result.season})"
    
    embed = discord.Embed(
        title=title,
        color=discord.Color.gold(),
    )
    
    embed.add_field(
        name="Keepers Selected",
        value=f"{result.keepers_selected} players",
        inline=True
    )
    
    embed.add_field(
        name="Keeper Salaries",
        value=f"${result.keeper_salary_cost}",
        inline=True
    )
    
    if result.rat_cost > 0:
        embed.add_field(
            name="Reduce-a-Tier",
            value=f"${result.rat_cost} (tax-free)",
            inline=True
        )
    
    if result.buyin_cost > 0:
        embed.add_field(
            name="Buy-Ins",
            value=f"${result.buyin_cost}",
            inline=True
        )
    
    embed.add_field(
        name="Taxable Spend",
        value=f"${result.total_taxable_spend}",
        inline=True
    )
    
    if result.draft_picks_taxed:
        embed.add_field(
            name="Draft Pick Tax",
            value=f"Rounds {', '.join(map(str, result.draft_picks_taxed))}",
            inline=False
        )
    else:
        embed.add_field(
            name="Draft Pick Tax",
            value="None",
            inline=False
        )
    
    embed.set_footer(text=f"Total WB Spent: ${result.wb_spent} | Remaining: ${result.wb_remaining} | {result.timestamp}")
    
    try:
        await channel.send(embed=embed)
        print(f"✅ KAP submission announced to Discord for {result.team}")
    except Exception as exc:
        print(f"⚠️ Failed to send KAP announcement: {exc}")

# commands/trade.py - Complete Updated Trade Command

import discord
from discord.ext import commands
from discord.ui import View, Button
from commands.utils import MANAGER_DISCORD_IDS, DISCORD_ID_TO_TEAM
from commands.trade_logic import create_trade_thread
import re
import json
from difflib import get_close_matches

def load_combined_players():
    try:
        with open("data/combined_players.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ combined_players.json not found. Run data pipeline first.")
        return []

def load_wizbucks():
    try:
        with open("data/wizbucks.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

class Trade(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="trade", description="Submit a trade proposal")
    @discord.app_commands.describe(
        team1_assets="Your assets (comma-separated: player names and/or $50WB)",
        team2="Second team abbreviation (HAM, WIZ, WAR, etc.)",
        team2_assets="Assets from team 2 (comma-separated)",
        team3="(Optional) Third team abbreviation",
        team3_assets="(Optional) Assets from team 3"
    )
    async def trade(self, interaction: discord.Interaction,
                    team1_assets: str,
                    team2: str,
                    team2_assets: str,
                    team3: str = None,
                    team3_assets: str = None):

        await interaction.response.defer(ephemeral=True)
        
        user_id = interaction.user.id
        team1 = DISCORD_ID_TO_TEAM.get(user_id)
        
        if not team1:
            await interaction.followup.send(
                "❌ **You are not registered as a team manager.**\n" +
                "Please contact an admin to get your Discord account linked to your team.",
                ephemeral=True
            )
            return

        # Validate team abbreviations
        valid_teams = set(DISCORD_ID_TO_TEAM.values())
        if team2.upper() not in valid_teams:
            team_list = ", ".join(sorted(valid_teams))
            await interaction.followup.send(
                f"❌ **Invalid team abbreviation: '{team2}'**\n" +
                f"Valid teams: {team_list}",
                ephemeral=True
            )
            return
            
        if team3 and team3.upper() not in valid_teams:
            team_list = ", ".join(sorted(valid_teams))
            await interaction.followup.send(
                f"❌ **Invalid team abbreviation: '{team3}'**\n" +
                f"Valid teams: {team_list}",
                ephemeral=True
            )
            return

        # Parse assets
        players = {
            "team1": [s.strip() for s in team1_assets.split(",") if s.strip()],
            "team2": [s.strip() for s in team2_assets.split(",") if s.strip()],
            "team3": [s.strip() for s in team3_assets.split(",")] if team3 and team3_assets else []
        }

        # Extract Wiz Bucks
        wb = {
            "team1": extract_wb(players["team1"]),
            "team2": extract_wb(players["team2"]),
            "team3": extract_wb(players["team3"]) if team3 else 0
        }

        await self.handle_trade_submission(interaction, user_id, team1, team2.upper(), team3.upper() if team3 else None, players, wb)

    async def handle_trade_submission(self, interaction, user_id, team1, team2, team3, players, wb):
        involved = [team1, team2] + ([team3] if team3 else [])
        problems = []
        suggestions = []
        corrected_players = {}
        all_players = load_combined_players()
        wizbucks = load_wizbucks()

        team_key_map = {team1: "team1", team2: "team2"}
        if team3:
            team_key_map[team3] = "team3"

        # Validate Wiz Bucks balances first
        for team in involved:
            wb_spent = wb.get(team_key_map.get(team), 0)
            team_full_name = get_full_team_name(team)
            current_balance = wizbucks.get(team_full_name, 0)
            
            if wb_spent > current_balance:
                problems.append(f"**{team}** doesn't have enough Wiz Bucks (needs ${wb_spent}, has ${current_balance})")

        # Validate players exist on their respective rosters
        for team in involved:
            corrected_players[team] = []
            submitted = players.get(team_key_map.get(team), [])
            team_roster = [p for p in all_players if p.get("manager") == team]
            
            if not team_roster:
                problems.append(f"**{team}** roster not found in database. Contact admin.")
                continue

            for raw_input in submitted:
                if is_wizbuck_entry(raw_input):
                    corrected_players[team].append(raw_input)
                    continue

                # Try to find the player
                found_player, match_type = find_player_on_roster(raw_input, team_roster)
                
                if found_player:
                    formatted = format_player_display(found_player)
                    corrected_players[team].append(formatted)
                    
                    if match_type == "fuzzy":
                        suggestions.append(f"**{team}**: Matched '{raw_input}' → '{found_player['name']}'")
                else:
                    problems.append(f"**{team}**: '{raw_input}' not found on roster")
                    
                    # Suggest similar names
                    similar = find_similar_players(raw_input, team_roster, n=3)
                    if similar:
                        similar_names = [p["name"] for p in similar]
                        suggestions.append(f"**{team}**: Did you mean: {', '.join(similar_names)}?")

        # Build response message
        if problems:
            msg = "❌ **Trade submission failed:**\n\n"
            msg += "\n".join(f"• {p}" for p in problems)
            
            if suggestions:
                msg += "\n\n💡 **Suggestions:**\n"
                msg += "\n".join(f"• {s}" for s in suggestions)
            
            msg += f"\n\n🔍 Use `/roster team:{team1}` to see your exact player names."
            await interaction.followup.send(content=msg, ephemeral=True)
            return

        # Show suggestions if we had fuzzy matches
        if suggestions:
            msg = "⚠️ **Player name corrections made:**\n\n"
            msg += "\n".join(f"• {s}" for s in suggestions)
            msg += "\n\nIf these look correct, the trade preview is below. If not, please re-submit with exact names."
            await interaction.followup.send(content=msg, ephemeral=True)

        # Create trade preview
        preview_msg = create_trade_preview(involved, corrected_players, wb, team_key_map)
        
        view = PreviewConfirmView(
            trade_data={
                "initiator_id": user_id,
                "teams": involved,
                "players": corrected_players,
                "wizbucks": {team: wb.get(team_key_map[team], 0) for team in involved}
            }
        )

        await interaction.followup.send(content=preview_msg, view=view, ephemeral=True)

def find_player_on_roster(search_name, roster):
    """
    Find a player on the roster with improved matching
    Returns (player_dict, match_type) or (None, None)
    """
    # First: Try exact match (case insensitive)
    for player in roster:
        if player["name"].lower() == search_name.lower():
            return player, "exact"
    
    # Second: Use fuzzy matching with the same logic as the working test script
    roster_names = [p["name"] for p in roster]
    matches = get_close_matches(search_name, roster_names, n=1, cutoff=0.8)
    
    if matches:
        matched_name = matches[0]
        for player in roster:
            if player["name"] == matched_name:  # Exact match on the original case
                return player, "fuzzy"
    
    # Third: Try lower cutoff for broader matching
    matches = get_close_matches(search_name, roster_names, n=1, cutoff=0.6)
    
    if matches:
        matched_name = matches[0]
        for player in roster:
            if player["name"] == matched_name:
                return player, "partial"
    
    return None, None

def find_similar_players(search_name, roster, n=3):
    """Find similar player names for suggestions"""
    roster_names = [p["name"] for p in roster]
    matches = get_close_matches(search_name, roster_names, n=n, cutoff=0.4)
    return [p for p in roster if p["name"] in matches]

def format_player_display(player):
    """Format player for display in trade"""
    pos = player.get("position", "?")
    name = player.get("name", "Unknown")
    team = player.get("team", "FA")
    contract = player.get("years_simple", "?")
    return f"{pos} {name} [{team}] [{contract}]"

def create_trade_preview(teams, corrected_players, wb, team_key_map):
    """Create the trade preview message"""
    def create_block(team):
        lines = corrected_players.get(team, [])
        wb_val = wb.get(team_key_map[team], 0)
        if wb_val > 0:
            lines.append(f"${wb_val} WB")
        return f"🔁 **{team} receives:**\n" + ("\n".join(lines) if lines else "*Nothing*")

    msg = "📬 **TRADE PREVIEW**\n\n"
    for team in teams:
        msg += create_block(team) + "\n\n"
    
    msg += "✏️ To edit this trade, re-submit the `/trade` command."
    return msg

def get_full_team_name(abbr):
    """Convert team abbreviation to full name for wizbucks lookup"""
    team_map = {
        "HAM": "Hammers",
        "RV": "Rick Vaughn", 
        "B2J": "Btwn2Jackies",
        "CFL": "Country Fried Lamb",
        "DMN": "The Damn Yankees",
        "LFB": "La Flama Blanca",
        "JEP": "Jepordizers!",
        "TBB": "The Bluke Blokes",
        "WIZ": "Whiz Kids",
        "DRO": "Andromedans",
        "SAD": "not much of a donkey",
        "WAR": "Weekend Warriors"
    }
    return team_map.get(abbr, abbr)

class PreviewConfirmView(View):
    def __init__(self, trade_data):
        super().__init__(timeout=300)
        self.trade_data = trade_data

    @discord.ui.button(label="✅ Confirm Trade", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.trade_data["initiator_id"]:
            await interaction.response.send_message("❌ Only the trade submitter can confirm.", ephemeral=True)
            return

        await interaction.response.send_message("✅ Trade confirmed! Creating approval thread...", ephemeral=True)
        await create_trade_thread(interaction.guild, self.trade_data)

    @discord.ui.button(label="❌ Cancel Trade", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id == self.trade_data["initiator_id"]:
            await interaction.response.send_message("❌ Trade canceled.", ephemeral=True)

# Helper functions
def extract_wb(asset_list):
    """Extract Wiz Bucks amount from asset list"""
    for item in asset_list:
        if not isinstance(item, str):
            continue
        cleaned = item.strip().lower().replace("$", "").replace(" ", "")
        if "wb" in cleaned:
            match = re.search(r'(\d+)', cleaned)
            if match:
                return int(match.group(1))
    return 0

def is_wizbuck_entry(s):
    """Check if string represents Wiz Bucks"""
    if not isinstance(s, str):
        return False
    s = s.lower().strip()
    return bool(re.search(r'\$?\d+\s*wb?', s))

async def setup(bot):
    await bot.add_cog(Trade(bot))
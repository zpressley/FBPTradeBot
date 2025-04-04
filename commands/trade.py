import discord
from discord.ext import commands
from discord.ui import View, Button
from commands.lookup import extract_name, combined_data
from commands.utils import MANAGER_DISCORD_IDS, DISCORD_ID_TO_TEAM
from commands.trade_logic import create_trade_thread
import re
import json

# Load Wiz Bucks data
with open("data/wizbucks.json", "r") as f:
    wizbucks_data = json.load(f)

# Slash command setup
class Trade(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="trade", description="Submit a trade proposal")
    @discord.app_commands.describe(
        team1_assets="Your assets (comma-separated list of players and/or $WB)",
        team2="Second team (abbreviation)",
        team2_assets="Assets from team 2",
        team3="(Optional) Third team (abbreviation)",
        team3_assets="(Optional) Assets from team 3"
    )
    async def trade(self, interaction: discord.Interaction,
                    team1_assets: str,
                    team2: str,
                    team2_assets: str,
                    team3: str = None,
                    team3_assets: str = None):

        user_id = interaction.user.id

        # Parse asset strings
        players = {
            "team1": [s.strip() for s in team1_assets.split(",") if s.strip()],
            "team2": [s.strip() for s in team2_assets.split(",") if s.strip()],
            "team3": [s.strip() for s in team3_assets.split(",")] if team3 and team3_assets else []
        }

        wb = {
            "team1": extract_wb(players["team1"]),
            "team2": extract_wb(players["team2"]),
            "team3": extract_wb(players["team3"]) if team3 else 0
        }

        await handle_trade_submission(interaction, user_id, team2, team3, players, wb)


# Main validation + preview
async def handle_trade_submission(interaction, user_id, team2, team3, players, wb):
    team1 = DISCORD_ID_TO_TEAM.get(user_id)
    if not team1:
        await interaction.response.send_message("❌ You are not mapped to a team.", ephemeral=True)
        return

    involved = [team1, team2] + ([team3] if team3 else [])
    problems = []
    corrected_players = {}

    # WB validation
    for team in involved:
        max_allowed = wizbucks_data.get(team, 0)
        if wb.get(team, 0) % 5 != 0:
            problems.append(f"{team}: WB must be in $5 increments.")
        if wb.get(team, 0) > max_allowed:
            problems.append(f"{team}: Trying to send ${wb.get(team)} WB but only has ${max_allowed}.")

    # Player validation
    for team in involved:
        roster = combined_data.get(team, [])
        corrected_players[team] = []

        submitted = players.get(f"team{involved.index(team)+1}", [])
        for raw in submitted:
            if is_wizbuck_entry(raw):
                corrected_players[team].append(raw)
                continue

            submitted_clean = extract_name(raw)
            roster_names = [extract_name(p) for p in roster]

            from difflib import get_close_matches
            match = get_close_matches(submitted_clean, roster_names, n=1, cutoff=0.8)

            if match:
                matched_index = roster_names.index(match[0])
                corrected_players[team].append(roster[matched_index])
            else:
                problems.append(f"{team}: `{raw}` is not on your roster.")

    if problems:
        msg = (
            "❌ Trade could not be submitted due to the following issues:\n\n" +
            "\n".join(f"- {p}" for p in problems) +
            "\n\n🔁 Please re-submit the trade using `/trade` and only include players currently on your team.\n" +
            "💡 Use `/roster` (coming soon) to view your current players."
        )
        await interaction.response.send_message(content=msg, ephemeral=True)
        return

    # Build preview
    def block(team):
        lines = corrected_players.get(team, [])
        wb_val = wb.get(team, 0)
        if wb_val > 0:
            lines.append(f"${wb_val} WB")
        return f"🔁 **{team} receives:**\n" + "\n".join(lines)

    msg = f"""📬 **TRADE PREVIEW**

{block(team1)}

{block(team2)}"""
    if team3:
        msg += f"\n\n{block(team3)}"

    msg += "\n\n✏️ To edit this trade, re-send the `/trade` command."

    view = PreviewConfirmView(
        trade_data={
            "initiator_id": user_id,
            "initiator_name": team1,
            "team1_assets": corrected_players[team1],
            "team2": team2,
            "team2_assets": corrected_players[team2],
            "team3": team3,
            "team3_assets": corrected_players.get(team3, [])
        }
    )

    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(content=msg, view=view, ephemeral=True)


# Preview confirmation view
class PreviewConfirmView(View):
    def __init__(self, trade_data):
        super().__init__(timeout=300)
        self.trade_data = trade_data

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.trade_data["initiator_id"]:
            await interaction.response.send_message("Only the original submitter can confirm.", ephemeral=True)
            return

        reformatted_data = {
            "teams": [
                self.trade_data["initiator_name"],
                self.trade_data["team2"]
            ] + ([self.trade_data["team3"]] if self.trade_data.get("team3") else []),
            "players": {
                self.trade_data["initiator_name"]: self.trade_data["team1_assets"],
                self.trade_data["team2"]: self.trade_data["team2_assets"],
            },
            "wizbucks": {
                self.trade_data["initiator_name"]: extract_wb(self.trade_data["team1_assets"]),
                self.trade_data["team2"]: extract_wb(self.trade_data["team2_assets"]),
            }
        }

        if self.trade_data.get("team3"):
            reformatted_data["players"][self.trade_data["team3"]] = self.trade_data["team3_assets"]
            reformatted_data["wizbucks"][self.trade_data["team3"]] = extract_wb(self.trade_data["team3_assets"])

        await interaction.response.send_message("✅ Trade confirmed! Creating private review thread...", ephemeral=True)
        await create_trade_thread(interaction.guild, reformatted_data)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id == self.trade_data["initiator_id"]:
            await interaction.response.send_message("❌ Trade canceled.", ephemeral=True)


# Helper to parse WB
def extract_wb(asset_list):
    for item in asset_list:
        cleaned = item.strip().lower()
        match = re.match(r"\$?(\d+)\s*(wb)?", cleaned)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return 0


import re

def is_wizbuck_entry(s):
    if not isinstance(s, str):
        return False
    s = s.lower().strip()
    return bool(re.match(r"^\$?\d+\s*wb?$", s))


# Register the slash command cog
async def setup(bot):
    await bot.add_cog(Trade(bot))

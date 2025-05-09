import discord
from discord.ext import commands
from discord.ui import View, Button
from commands.lookup import extract_name, all_players
from commands.utils import MANAGER_DISCORD_IDS, DISCORD_ID_TO_TEAM
from commands.trade_logic import create_trade_thread
import re
import json
from difflib import get_close_matches

with open("data/wizbucks.json", "r") as f:
    wizbucks_data = json.load(f)

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

async def handle_trade_submission(interaction, user_id, team2, team3, players, wb):
    await interaction.response.defer(ephemeral=True)

    team1 = DISCORD_ID_TO_TEAM.get(user_id)
    if not team1:
        await interaction.followup.send("❌ You are not mapped to a team.", ephemeral=True)
        return

    involved = [team1, team2] + ([team3] if team3 else [])
    problems = []
    corrected_players = {}

    team_key_map = {
        team1: "team1",
        team2: "team2"
    }
    if team3:
        team_key_map[team3] = "team3"

    for team in involved:
        corrected_players[team] = []
        submitted = players.get(team_key_map.get(team), [])
        team_roster = [p for p in all_players if p.get("manager") == team]

        for raw in submitted:
            if is_wizbuck_entry(raw):
                corrected_players[team].append(raw)
                continue

            submitted_clean = extract_name(raw).lower()
            roster_names = [extract_name(p["name"]).lower() for p in team_roster]

            match = get_close_matches(submitted_clean, roster_names, n=1, cutoff=0.8)

            if match:
                matched_name = match[0]
                matched_player = next(p for p in team_roster if extract_name(p["name"]).lower() == matched_name)
                formatted = f"{matched_player['position']} {matched_player['name']} [{matched_player['team']}] [{matched_player['years_simple'] or 'NA'}]"
                corrected_players[team].append(formatted)
            else:
                problems.append(f"{team}: `{raw}` is not on your roster.")

    if problems:
        msg = (
            "❌ Trade could not be submitted due to the following issues:\n\n" +
            "\n".join(f"- {p}" for p in problems) +
            "\n\n🔁 Please re-submit the trade using `/trade` and only include players currently on your team.\n" +
            "💡 Use `/roster` to view your current players."
        )
        await interaction.followup.send(content=msg, ephemeral=True)
        return

    def block(team):
        lines = corrected_players.get(team, [])
        wb_val = wb.get(team_key_map[team], 0)
        if wb_val > 0:
            lines.append(f"${wb_val} WB")
        return f"🔁 **{team} receives:**\n" + "\n".join(lines)

    # Invert preview: show what each team RECEIVES
    msg = f"""📬 **TRADE PREVIEW**

{block(team2)}

{block(team1)}"""
    if team3:
        msg += f"\n\n{block(team3)}"

    msg += "\n\n✏️ To edit this trade, re-submit the `/trade` command."

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

    await interaction.followup.send(content=msg, view=view, ephemeral=True)

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

def extract_name(player_line):
    try:
        match = re.match(r"^\w+\s+(.+?)\s+\[", player_line)
        return match.group(1).strip() if match else player_line.strip()
    except Exception:
        return player_line.strip()

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

def is_wizbuck_entry(s):
    if not isinstance(s, str):
        return False
    s = s.lower().strip()
    return bool(re.match(r"^\$?\d+\s*wb?$", s))

async def setup(bot):
    await bot.add_cog(Trade(bot))

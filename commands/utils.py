# commands/utils.py

import datetime

# Full name + abbreviation mapping for manager mentions
MANAGER_DISCORD_IDS = {
    "HAM": 347571660230230017, "Hammers": 347571660230230017,
    "RV": 689911142432112657, "Rick Vaughn": 689911142432112657,
    "B2J": 689952988957245578, "Btwn2Jackies": 689952988957245578,
    "CFL": 689887002887454815, "Country Fried Lamb": 689887002887454815,
    "LAW": 892152416718422056, "Law-Abiding Citizens": 892152416718422056,
    "LFB": 890059214586773574, "La Flama Blanca": 890059214586773574,
    "JEP": 814294382529347594, "Jepordizers!": 814294382529347594,
    "TBB": 161932197308137473, "The Bluke Blokes": 161932197308137473,
    "WIZ": 161967242118955008, "Whiz Kids": 161967242118955008,
    "DRO": 541092942455242754, "Andromedans": 541092942455242754,
    "SAD": 875750135005597728, "not much of a donkey": 875750135005597728,
    "WAR": 664280448788201522, "Weekend Warriors": 664280448788201522
}

# Mentions managers based on team label
def mention_manager(team_label):
    manager_id = MANAGER_DISCORD_IDS.get(team_label)
    return f"<@{manager_id}>" if manager_id else f"`{team_label}`"

# Used for timestamping trades with submission and processing day
def get_trade_dates():
    now = datetime.datetime.now()
    submission_day = now.strftime("%a").upper()
    submission_date = now.strftime("%-m/%-d")  # Use %#m/%#d on Windows

    processing_days = [6, 1, 3]  # Sunday, Tuesday, Thursday
    for i in range(1, 7):
        future = now + datetime.timedelta(days=i)
        if future.weekday() in processing_days:
            processing_day = future.strftime("%a").upper()
            processing_date = future.strftime("%-m/%-d")
            break

    return f"Sub: {submission_day} {submission_date}", f"Proc: {processing_day} {processing_date}"
# Reverse map of MANAGER_DISCORD_IDS for quick lookup by Discord ID
DISCORD_ID_TO_TEAM = {v: k for k, v in MANAGER_DISCORD_IDS.items() if len(k) <= 4}

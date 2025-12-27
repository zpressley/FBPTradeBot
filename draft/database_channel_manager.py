"""
Database Channel Manager - Phase 2
Integrated with DatabaseTracker for live pick updates
"""

import discord
from typing import Dict, List, Optional, Tuple
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prospect_stats_repository import ProspectStatsRepository
from draft.database_tracker import DatabaseTracker
import asyncio


class DatabaseChannelManager:
    """
    Manages prospect database channel with live pick tracking.
    
    Features:
    - Posts full ranked lists in main channel
    - Duplicates in position threads
    - Tracks player locations for live updates
    - Updates in place when picks happen

    Compatibility:
    `commands/draft.py` currently expects these methods:
    - setup_database_channel(...)
    - refresh_all(...)
    - format_thread_index()

    This file originally implemented `db_setup(...)` (Phase 2). We provide thin wrappers
    so `/draft db_setup` and `/draft db_refresh` work without requiring changes elsewhere.
    """

    MESSAGE_LIMIT = 1800

    def __init__(self, bot, channel_id: int, season: int = 2025, draft_type: str = "prospect"):
        self.bot = bot
        self.channel_id = channel_id
        self.season = season
        self.draft_type = draft_type  # For compatibility with existing code

        # Initialize stats repository
        self.stats_repo = ProspectStatsRepository()

        # Initialize tracker
        self.tracker = DatabaseTracker(bot)

        # Position groupings
        self.POSITION_GROUPS = {
            "Catchers": ["C"],
            "Infielders": ["1B", "2B", "3B", "SS", "IF"],
            "Outfielders": ["OF", "LF", "CF", "RF"],
            "Starting Pitchers": ["SP", "P"],
            "Relief Pitchers": ["RP", "CP"]
        }

    async def setup_database_channel(self, guild: discord.Guild, prospect_db) -> None:
        """Compatibility wrapper: calls Phase 2 `db_setup(...)`."""
        await self.db_setup(guild, prospect_db)

    async def refresh_all(self, guild: discord.Guild, prospect_db) -> None:
        """Compatibility wrapper for `/draft db_refresh`.

        Right now this rebuilds the channel from scratch.
        """
        await self.db_setup(guild, prospect_db)

    def format_thread_index(self) -> str:
        """Return an index of the created position-group threads.

        Uses the persisted `DatabaseTracker.position_groups` data.
        """
        if not getattr(self.tracker, "position_groups", None):
            return "No threads created yet"

        lines = []
        # Sort for stable output
        for group_name in sorted(self.tracker.position_groups.keys()):
            info = self.tracker.position_groups[group_name]
            thread_id = info.get("thread_id")
            if thread_id:
                # Threads mention like channels.
                lines.append(f"• {group_name}: <#{thread_id}>")
            else:
                lines.append(f"• {group_name}")
        return "\n".join(lines)

    # ========== SETUP COMMAND ==========
    
    async def db_setup(self, guild: discord.Guild, prospect_db) -> bool:
        """
        Complete database setup.
        
        Steps:
        1. Clear all existing messages in channel
        2. Load prospects with stats
        3. Sort by rank
        4. Create position threads
        5. Post ranked lists in main channel
        6. Duplicate in threads
        7. Track all player locations
        8. Save tracking data
        
        Returns:
            True if successful
        """
        
        channel = guild.get_channel(self.channel_id)
        if not channel:
            print(f"❌ Channel {self.channel_id} not found")
            return False
        
        print(f"📊 Starting database setup in #{channel.name}")
        print()
        
        # Step 1: Clear existing messages
        print("Step 1: Clearing existing messages...")
        await self._clear_channel(channel)
        print()
        
        # Step 2: Clear tracker data
        print("Step 2: Clearing tracker data...")
        self.tracker.clear_all()
        print()
        
        # Step 3: Setup position groups
        print("Step 3: Creating position groups...")
        
        for group_name, positions in self.POSITION_GROUPS.items():
            print(f"\n🔹 {group_name}")
            
            # Get all players in this position group
            players = []
            for pos in positions:
                players.extend(prospect_db.get_by_position(pos))
            
            if not players:
                print(f"   ⚠️ No players found")
                continue
            
            # Sort by rank
            players.sort(key=lambda p: p.get('rank', 9999))
            
            print(f"   📊 {len(players)} prospects loaded")
            
            # Create thread (for organization)
            thread = await self._create_position_thread(channel, group_name, len(players))
            
            # Post ranked list to MAIN CHANNEL and THREAD
            main_messages, thread_messages = await self._post_ranked_lists(
                channel,
                thread,
                players,
                group_name
            )
            
            # Register with tracker
            self.tracker.register_position_group(
                position_group=group_name,
                main_message_ids=[m.id for m in main_messages],
                thread_message_ids=[m.id for m in thread_messages],
                thread_id=thread.id,
                channel_id=channel.id,
                total_prospects=len(players)
            )
            
            print(f"   ✅ Posted {len(main_messages)} messages, {len(players)} players tracked")
        
        # Save tracker data
        print()
        print("Step 4: Saving tracker data...")
        self.tracker.save_tracking_data()
        
        print()
        print("=" * 70)
        print("✅ DATABASE SETUP COMPLETE!")
        print(f"   Total players tracked: {len(self.tracker.player_locations)}")
        print(f"   Position groups: {len(self.tracker.position_groups)}")
        
        return True
    
    async def _clear_channel(self, channel: discord.TextChannel):
        """Delete all existing messages in channel"""
        
        deleted = 0
        
        # Delete in batches (Discord API limit)
        async for message in channel.history(limit=200):
            try:
                await message.delete()
                deleted += 1
                
                if deleted % 10 == 0:
                    print(f"   🗑️ Deleted {deleted} messages...")
                    await asyncio.sleep(0.5)  # Rate limit safety
                    
            except Exception as e:
                print(f"   ⚠️ Could not delete message: {e}")
        
        print(f"   ✅ Cleared {deleted} messages")
    
    async def _create_position_thread(
        self,
        channel: discord.TextChannel,
        group_name: str,
        player_count: int
    ) -> discord.Thread:
        """Create a thread for a position group"""
        
        # Create a starter message for the thread
        starter_msg = await channel.send(
            f"**📊 {group_name.upper()}** ({player_count} prospects)\n"
            f"└─ See full ranked list below"
        )
        
        # Create thread from message
        thread = await starter_msg.create_thread(
            name=f"{group_name}",
            auto_archive_duration=10080  # 7 days
        )
        
        return thread
    
    async def _post_ranked_lists(
        self,
        channel: discord.TextChannel,
        thread: discord.Thread,
        players: List[Dict],
        position_group: str
    ) -> Tuple[List[discord.Message], List[discord.Message]]:
        """
        Post ranked player lists to both main channel and thread.
        Returns (main_messages, thread_messages)
        """
        
        # Create chunks with player tracking
        chunks_with_tracking = self._create_tracked_chunks(players, position_group)
        
        main_messages = []
        thread_messages = []
        
        for chunk_text, players_in_chunk in chunks_with_tracking:
            # Post to main channel
            main_msg = await channel.send(chunk_text)
            main_messages.append(main_msg)
            
            # Post to thread (duplicate)
            thread_msg = await thread.send(chunk_text)
            thread_messages.append(thread_msg)
            
            # Register each player's location
            for player_name, status_line_idx, rank in players_in_chunk:
                self.tracker.register_player_location(
                    player_name=player_name,
                    main_message_id=main_msg.id,
                    thread_message_id=thread_msg.id,
                    channel_id=channel.id,
                    thread_id=thread.id,
                    position_group=position_group,
                    status_line_index=status_line_idx,
                    rank=rank
                )
            
            # Small delay between posts
            await asyncio.sleep(0.3)
        
        return main_messages, thread_messages
    
    def _create_tracked_chunks(
        self,
        players: List[Dict],
        position_group: str
    ) -> List[Tuple[str, List[Tuple[str, int, int]]]]:
        """
        Create message chunks with player location tracking.
        
        Returns:
            List of (chunk_text, [(player_name, status_line_index, rank), ...])
        """
        
        chunks = []
        current_chunk_lines = []
        current_line_number = 0
        players_in_current_chunk = []
        
        # Add tier info
        tiers = self._get_tier_info(len(players))
        
        for i, player in enumerate(players):
            num = i + 1
            
            # Check for tier header
            tier_header = self._get_tier_header(num, tiers)
            if tier_header:
                tier_lines = tier_header.split('\n')
                
                # Check if tier fits
                test_content = '\n'.join(current_chunk_lines + tier_lines)
                if len(test_content) > self.MESSAGE_LIMIT:
                    # Finalize current chunk
                    chunk_text = '\n'.join(current_chunk_lines)
                    chunks.append((chunk_text, players_in_current_chunk))
                    
                    # Reset
                    current_chunk_lines = []
                    current_line_number = 0
                    players_in_current_chunk = []
                
                # Add tier header
                current_chunk_lines.extend(tier_lines)
                current_line_number += len(tier_lines)
            
            # Format player
            player_text = self._format_player_with_stats(num, player)
            player_lines = player_text.split('\n')
            
            # Calculate status line index
            # Player format:
            # Line 0: {num}. **{name}** ({pos}) [{team}]
            # Line 1:     📊 {stats}
            # Line 2:     {status}  <-- THIS LINE
            # Line 3: (blank)
            
            status_line_index = current_line_number + 2
            
            # Check if player fits
            test_content = '\n'.join(current_chunk_lines + player_lines)
            if len(test_content) > self.MESSAGE_LIMIT:
                # Finalize current chunk
                chunk_text = '\n'.join(current_chunk_lines)
                chunks.append((chunk_text, players_in_current_chunk))
                
                # Reset
                current_chunk_lines = []
                current_line_number = 0
                players_in_current_chunk = []
                
                # Recalculate status line for new chunk
                status_line_index = current_line_number + 2
            
            # Add player to chunk
            current_chunk_lines.extend(player_lines)
            
            # Track player location
            players_in_current_chunk.append((
                player['name'],
                status_line_index,
                player.get('rank', 9999)
            ))
            
            current_line_number += len(player_lines)
        
        # Finalize last chunk
        if current_chunk_lines:
            chunk_text = '\n'.join(current_chunk_lines)
            chunks.append((chunk_text, players_in_current_chunk))
        
        return chunks
    
    def _format_player_with_stats(self, num: int, player: Dict) -> str:
        """
        Format player with stats BEFORE status.
        
        Format:
        {num}. **{name}** ({pos}) [{team}]
            📊 {stats}
            {status}
        
        """
        name = player['name']
        pos = player.get('position', '?')
        team = player.get('team', '?').upper()
        
        # Line 1: Number, name, position, team
        line1 = f"{num}. **{name}** ({pos}) [{team}]\n"
        
        # Line 2: Stats
        stats_line = self._get_player_stats(name, pos)
        line2 = f"    📊 {stats_line}\n"
        
        # Line 3: Status
        ownership = player.get('ownership', 'UC')
        owner = player.get('owner', '')
        
        if ownership == "UC" or not owner:
            status = "✅ AVAILABLE"
        elif ownership in ["FC", "PC", "DC"]:
            status = f"❌ {ownership} ({owner})"
        else:
            status = ownership
        
        line3 = f"    {status}\n"
        
        # Blank line for spacing
        return line1 + line2 + line3 + "\n"
    
    def _get_player_stats(self, player_name: str, position: str) -> str:
        """Get formatted stats string for a player"""
        
        prospect = self.stats_repo.get_prospect(player_name)
        
        if not prospect:
            return "No stats loaded"
        
        is_pitcher = position in ['P', 'SP', 'RP', 'CP']
        
        batting = prospect.get('batting', {}).get('season', {})
        pitching = prospect.get('pitching', {}).get('season', {})
        
        if is_pitcher and pitching.get('innings_pitched', 0) > 0:
            g = pitching.get('games', 0)
            era = pitching.get('era', 0)
            k = pitching.get('strikeouts', 0)
            return f"{g}G, {era:.2f} ERA, {k}K"
        
        elif not is_pitcher and batting.get('at_bats', 0) > 0:
            g = batting.get('games', 0)
            avg = batting.get('avg', 0)
            hr = batting.get('home_runs', 0)
            rbi = batting.get('rbi', 0)
            return f"{g}G, .{int(avg*1000):03d}, {hr}HR, {rbi}RBI"
        
        else:
            return "No MLB stats"
    
    def _get_tier_info(self, total_players: int) -> Dict[str, int]:
        """Calculate tier boundaries"""
        tiers = {}
        
        if total_players > 0:
            tiers["TOP TIER"] = 1
        if total_players > 50:
            tiers["MID TIER"] = 51
        if total_players > 150:
            tiers["DEEP TIER"] = 151
        
        return tiers
    
    def _get_tier_header(self, player_num: int, tiers: Dict[str, int]) -> Optional[str]:
        """Get tier header if this player starts a new tier"""
        
        for tier_name, tier_start in tiers.items():
            if player_num == tier_start:
                if tier_name == "TOP TIER":
                    count = "1-50"
                elif tier_name == "MID TIER":
                    count = "51-150"
                else:
                    count = "151+"
                
                return f"━━━━━━━━━━━━━━━━━━━━━━━\n**{tier_name}** ({count})\n━━━━━━━━━━━━━━━━━━━━━━━"
        
        return None
    
    # ========== PICK UPDATES ==========
    
    def update_pick(
        self,
        player_name: str,
        round_num: int,
        pick_num: int,
        manager: str
    ):
        """
        Queue a pick update.
        
        Args:
            player_name: Full player name
            round_num: Draft round
            pick_num: Pick number in round
            manager: Manager abbreviation
        """
        
        self.tracker.queue_pick_update(
            player_name=player_name,
            round_num=round_num,
            pick_num=pick_num,
            manager=manager
        )


# ========== HELPER FUNCTIONS ==========

def create_database_manager(bot, channel_id: int) -> DatabaseChannelManager:
    """Create and initialize database manager"""
    manager = DatabaseChannelManager(bot, channel_id)
    print(f"✅ Database manager initialized")
    return manager
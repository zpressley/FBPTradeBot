"""
Database Tracker - Phase 2
Tracks where each prospect appears in Discord messages for live pick updates
"""

import discord
import json
import os
import asyncio
from typing import Dict, List, Optional, Tuple
from datetime import datetime


class DatabaseTracker:
    """
    Tracks prospect locations in Discord messages.
    Enables live pick updates by editing messages in place.
    
    Key Features:
    - Tracks player location in both main channel and thread
    - Queues pick updates to avoid race conditions
    - Persists to JSON (survives bot restarts)
    - Handles concurrent edits safely
    """
    
    def __init__(self, bot):
        self.bot = bot
        
        # Player location tracking
        self.player_locations = {}
        # {
        #   "Charlie Condon": {
        #       "main_message_id": 123456,
        #       "thread_message_id": 789012,
        #       "channel_id": 111111,
        #       "thread_id": 222222,
        #       "position_group": "Infielders",
        #       "status_line_index": 144,
        #       "rank": 35
        #   }
        # }
        
        # Position group tracking
        self.position_groups = {}
        # {
        #   "Infielders": {
        #       "main_message_ids": [123, 456, 789],
        #       "thread_message_ids": [321, 654, 987],
        #       "thread_id": 222222,
        #       "channel_id": 111111,
        #       "total_prospects": 134
        #   }
        # }
        
        # Update queue
        self.update_queue = []
        self.processing = False
        self.message_locks = {}  # message_id → Lock
        
        # Persistence
        self.tracker_file = "data/database_tracker.json"
        self.load_tracking_data()
    
    # ========== REGISTRATION ==========
    
    def register_player_location(
        self,
        player_name: str,
        main_message_id: int,
        thread_message_id: int,
        channel_id: int,
        thread_id: int,
        position_group: str,
        status_line_index: int,
        rank: int
    ):
        """
        Register where a player appears in Discord messages.
        
        Args:
            player_name: Full player name
            main_message_id: Message ID in main channel
            thread_message_id: Message ID in thread
            channel_id: Main channel ID
            thread_id: Thread ID
            position_group: Position group name
            status_line_index: Line number of status line in message
            rank: Player's rank
        """
        
        self.player_locations[player_name] = {
            "main_message_id": main_message_id,
            "thread_message_id": thread_message_id,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "position_group": position_group,
            "status_line_index": status_line_index,
            "rank": rank
        }
    
    def register_position_group(
        self,
        position_group: str,
        main_message_ids: List[int],
        thread_message_ids: List[int],
        thread_id: int,
        channel_id: int,
        total_prospects: int
    ):
        """Register a complete position group"""
        
        self.position_groups[position_group] = {
            "main_message_ids": main_message_ids,
            "thread_message_ids": thread_message_ids,
            "thread_id": thread_id,
            "channel_id": channel_id,
            "total_prospects": total_prospects
        }
    
    # ========== PICK UPDATE QUEUE ==========
    
    def queue_pick_update(
        self,
        player_name: str,
        round_num: int,
        pick_num: int,
        manager: str
    ):
        """
        Add a pick to the update queue.
        
        Args:
            player_name: Full player name
            round_num: Draft round number
            pick_num: Pick number within round
            manager: Manager abbreviation (WIZ, B2J, etc)
        """
        
        self.update_queue.append({
            "player_name": player_name,
            "round_num": round_num,
            "pick_num": pick_num,
            "manager": manager,
            "timestamp": datetime.now().isoformat(),
            "status": "pending"
        })
        
        queue_size = len(self.update_queue)
        print(f"📥 Queued pick: {player_name} by {manager} (Queue: {queue_size})")
        
        # Start queue processor if not already running
        if not self.processing:
            asyncio.create_task(self.process_queue())
    
    async def process_queue(self):
        """Process all queued pick updates sequentially"""
        
        if self.processing:
            return  # Already processing
        
        self.processing = True
        print(f"🔄 Starting queue processor ({len(self.update_queue)} updates)")
        
        successful = 0
        failed = 0
        
        while self.update_queue:
            update = self.update_queue[0]  # Peek at first
            update['status'] = 'processing'
            
            try:
                await self._execute_pick_update(
                    update['player_name'],
                    update['round_num'],
                    update['pick_num'],
                    update['manager']
                )
                
                update['status'] = 'complete'
                self.update_queue.pop(0)  # Remove
                successful += 1
                
            except Exception as e:
                print(f"❌ Error updating {update['player_name']}: {e}")
                update['status'] = 'failed'
                update['error'] = str(e)
                failed += 1
                
                # Remove failed update (don't retry infinitely)
                self.update_queue.pop(0)
            
            # Delay between updates (avoid rate limits)
            await asyncio.sleep(0.5)
        
        self.processing = False
        print(f"✅ Queue processed: {successful} successful, {failed} failed")
    
    async def _execute_pick_update(
        self,
        player_name: str,
        round_num: int,
        pick_num: int,
        manager: str
    ):
        """Execute a single pick update (updates both main and thread)"""
        
        # Get player location
        location = self.player_locations.get(player_name)
        
        if not location:
            print(f"⚠️ {player_name} not found in tracker")
            return
        
        # Create new status line
        new_status = f"    ❌ Picked R{round_num}/P{pick_num} by {manager}"
        
        # Update main channel message
        await self._edit_message_status(
            location['channel_id'],
            location['main_message_id'],
            location['status_line_index'],
            new_status,
            location_type="main channel"
        )
        
        # Update thread message
        await self._edit_message_status(
            location['thread_id'],
            location['thread_message_id'],
            location['status_line_index'],
            new_status,
            location_type="thread"
        )
        
        print(f"✅ Updated {player_name} in main + thread")
    
    async def _edit_message_status(
        self,
        channel_or_thread_id: int,
        message_id: int,
        status_line_index: int,
        new_status: str,
        location_type: str = "message"
    ):
        """
        Edit a specific status line in a Discord message.
        Thread-safe with locking.
        
        Args:
            channel_or_thread_id: Channel or thread ID
            message_id: Message ID to edit
            status_line_index: Which line to replace
            new_status: New status text
            location_type: "main channel" or "thread" (for logging)
        """
        
        # Get or create lock for this message
        if message_id not in self.message_locks:
            self.message_locks[message_id] = asyncio.Lock()
        
        # Acquire lock (ensures only one edit at a time per message)
        async with self.message_locks[message_id]:
            try:
                # Fetch channel/thread
                channel = self.bot.get_channel(channel_or_thread_id)
                if not channel:
                    print(f"⚠️ Channel/thread {channel_or_thread_id} not found")
                    return
                
                # Fetch message
                message = await channel.fetch_message(message_id)
                
                # Parse into lines
                lines = message.content.split('\n')
                
                # Verify line index is valid
                if status_line_index >= len(lines):
                    print(f"⚠️ Line index {status_line_index} out of range (message has {len(lines)} lines)")
                    return
                
                # Replace status line
                old_status = lines[status_line_index]
                lines[status_line_index] = new_status
                
                # Reconstruct message
                new_content = '\n'.join(lines)
                
                # Edit message
                await message.edit(content=new_content)
                
                print(f"   ✅ Edited {location_type} message {message_id}")
                
            except discord.NotFound:
                print(f"⚠️ Message {message_id} not found (may have been deleted)")
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limited
                    retry_after = getattr(e, 'retry_after', 1.0)
                    print(f"⚠️ Rate limited, waiting {retry_after}s...")
                    await asyncio.sleep(retry_after)
                    # Retry once
                    await message.edit(content=new_content)
                else:
                    raise
    
    # ========== PERSISTENCE ==========
    
    def save_tracking_data(self):
        """Save tracking data to JSON file"""
        
        data = {
            "player_locations": self.player_locations,
            "position_groups": self.position_groups,
            "metadata": {
                "last_saved": datetime.now().isoformat(),
                "total_players_tracked": len(self.player_locations),
                "total_position_groups": len(self.position_groups)
            }
        }
        
        os.makedirs("data", exist_ok=True)
        with open(self.tracker_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        print(f"💾 Saved tracking data: {len(self.player_locations)} players")
    
    def load_tracking_data(self):
        """Load tracking data from JSON file"""
        
        if not os.path.exists(self.tracker_file):
            print(f"📝 No existing tracker data found (first run)")
            return
        
        try:
            with open(self.tracker_file, 'r') as f:
                data = json.load(f)
            
            self.player_locations = data.get('player_locations', {})
            self.position_groups = data.get('position_groups', {})
            
            metadata = data.get('metadata', {})
            print(f"📂 Loaded tracking data: {len(self.player_locations)} players")
            print(f"   Last saved: {metadata.get('last_saved', 'Unknown')}")
            
        except Exception as e:
            print(f"⚠️ Error loading tracker data: {e}")
            self.player_locations = {}
            self.position_groups = {}
    
    # ========== UTILITY METHODS ==========
    
    def get_player_location(self, player_name: str) -> Optional[Dict]:
        """Get location info for a specific player"""
        return self.player_locations.get(player_name)
    
    def is_player_tracked(self, player_name: str) -> bool:
        """Check if a player is being tracked"""
        return player_name in self.player_locations
    
    def get_queue_size(self) -> int:
        """Get current queue size"""
        return len(self.update_queue)
    
    def get_stats(self) -> Dict:
        """Get tracker statistics"""
        return {
            "total_players": len(self.player_locations),
            "position_groups": len(self.position_groups),
            "queue_size": len(self.update_queue),
            "processing": self.processing
        }
    
    def clear_all(self):
        """Clear all tracking data (for fresh setup)"""
        self.player_locations = {}
        self.position_groups = {}
        self.update_queue = []
        self.message_locks = {}
        print("🗑️ Cleared all tracking data")
    
    # ========== MANUAL REFRESH ==========
    
    async def refresh_player(self, player_name: str, new_status: str):
        """
        Manually refresh a player's status (useful for corrections).
        
        Args:
            player_name: Full player name
            new_status: New status line (e.g., "    ✅ AVAILABLE")
        """
        
        location = self.player_locations.get(player_name)
        if not location:
            print(f"⚠️ {player_name} not tracked")
            return False
        
        # Update both locations
        await self._edit_message_status(
            location['channel_id'],
            location['main_message_id'],
            location['status_line_index'],
            new_status,
            location_type="main channel"
        )
        
        await self._edit_message_status(
            location['thread_id'],
            location['thread_message_id'],
            location['status_line_index'],
            new_status,
            location_type="thread"
        )
        
        print(f"✅ Manually refreshed {player_name}")
        return True


# ========== HELPER FUNCTIONS ==========

def create_tracker_instance(bot) -> DatabaseTracker:
    """Create and initialize a tracker instance"""
    tracker = DatabaseTracker(bot)
    print(f"✅ Tracker initialized")
    return tracker


if __name__ == "__main__":
    print("📋 Database Tracker - Phase 2")
    print("=" * 70)
    print()
    print("This is the core tracking system.")
    print()
    print("Features:")
    print("  ✅ Track player locations in Discord messages")
    print("  ✅ Queue pick updates")
    print("  ✅ Edit messages in place (no spam)")
    print("  ✅ Thread-safe with locking")
    print("  ✅ Persist to JSON")
    print()
    print("Next: Integrate with database_channel_manager.py")
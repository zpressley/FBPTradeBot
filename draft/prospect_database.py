"""
Prospect Database - Load and manage prospect data
Loads from combined_players.json and provides search/filter capabilities
"""

import json
import os
from typing import Dict, List, Optional
from difflib import get_close_matches


class ProspectDatabase:
    """
    Manages draft player database for prospect/keeper drafts.

    Source of truth (as requested): `data/combined_players.json`.

    Compatibility:
    - `draft/pick_validator.py` expects `self.prospects` (dict of player dicts keyed by name)
      with keys like: name, position, team, ownership, owner.
    """

    def __init__(self, season: int = 2025, draft_type: str = "prospect"):
        self.season = season
        self.draft_type = draft_type

        # Internal canonical store used by Discord and the website.
        self.players: Dict[str, Dict] = {}
        self.players_by_position: Dict[str, List[str]] = {}
        
        # PickValidator compatibility view (iterates self.db.prospects.items()).
        self.prospects: Dict[str, Dict] = {}
        
        self.load_from_combined_players()
    
    def load_from_combined_players(self) -> None:
        """Load players from combined_players.json for the given draft type.

        Prospect draft:
        - Primary pool is Farm players (player_type == "Farm").
        - We carry through:
          - name / upid / team / position / status / years_simple
          - player_type (Farm vs MLB/etc.)
          - contract_type (BC/DC/PC or blank)
          - fypd (bool; True if in the current FYPD pool)
          - manager (owner, if any)

        Keeper draft:
        - Uses MLB players (player_type == "MLB") with similar fields.
        """
        data_file = "data/combined_players.json"
        
        if not os.path.exists(data_file):
            print(f"⚠️ {data_file} not found - creating empty database")
            return
        
        with open(data_file, 'r') as f:
            all_players = json.load(f)
        
        # Filter by draft type
        if self.draft_type == "prospect":
            filtered = [p for p in all_players if p.get("player_type") == "Farm"]
        else:
            filtered = [p for p in all_players if p.get("player_type") == "MLB"]

        # Build player database. We assume data_pipeline/compute_prospect_ranks.py
        # has already attached precomputed `rank` and `fypd_rank` fields to each
        # prospect record in combined_players.json.

        # Build player database
        for player in filtered:
            name = (player.get("name") or "").strip()
            if not name:
                continue
            
            manager = player.get("manager", "")
            contract_type = player.get("contract_type", "")
            years_simple = player.get("years_simple", "")

            upid = str(player.get("upid") or "").strip()
            owner = manager.strip() if manager and str(manager).strip() else None

            # Ownership categories (PC/FC/DC/UC) are still exposed for
            # compatibility with existing displays, but the *rules* no
            # longer rely on them for the prospect draft. They are
            # derived purely from contract_type.
            ownership = self._parse_ownership(contract_type) if self.draft_type == "prospect" else ("PC" if owner else "UC")

            # Rankings come precomputed from the data pipeline.
            fypd_rank = player.get("fypd_rank")
            base_rank = player.get("rank")

            record = {
                "name": name,
                "position": player.get("position", ""),
                "team": player.get("team", ""),
                "manager": manager,
                "contract_type": contract_type,
                "years_simple": years_simple,
                "upid": upid,
                "yahoo_id": player.get("yahoo_id", ""),
                "player_type": player.get("player_type"),
                "fypd": bool(player.get("fypd", False)),

                # PickValidator / UI helpers
                "ownership": ownership,
                "owner": owner,
                "rank": base_rank,
                "fypd_rank": fypd_rank,
                "status_code": years_simple or "",
            }

            self.players[name] = record
            
            # Index by position
            pos = record.get("position", "")
            if pos:
                self.players_by_position.setdefault(pos, []).append(name)
        
        # Build PickValidator compatibility view.
        self.prospects = self.players
        
        print(f"✅ Loaded {len(self.players)} {self.draft_type} players")
        print(f"   Positions: {list(self.players_by_position.keys())}")
    
    def _parse_ownership(self, contract_type: str) -> str:
        """Parse ownership status from contract_type for internal filters.

        Historically, prospect rules used UC/PC/FC/DC. The new 2026
        Constitution removes protected/unprotected rounds, but we keep
        this helper for backward-compatible displays.
        """
        s = (contract_type or "").strip().lower()

        if not s:
            return "UC"
        if "purchased" in s:
            return "PC"
        if s.startswith("farm"):
            return "FC"
        if "development" in s:
            return "DC"

        if "pc" in s:
            return "PC"
        if "fc" in s:
            return "FC"
        if "dc" in s:
            return "DC"

        return "UC"
    
    def search(self, query: str, filters: Optional[Dict] = None) -> List[Dict]:
        """
        Search for players by name (fuzzy matching).
        
        Args:
            query: Search string
            filters: Optional filters (position, ownership, etc.)
            
        Returns:
            List of matching player dicts
        """
        if not query:
            return []
        
        # Fuzzy match on names
        all_names = list(self.players.keys())
        matches = get_close_matches(query, all_names, n=10, cutoff=0.6)
        
        results = [self.players[name] for name in matches]
        
        # Apply filters if provided
        if filters:
            if "position" in filters:
                results = [p for p in results if p["position"] == filters["position"]]
            
            if "ownership" in filters:
                results = [p for p in results if p["ownership"] == filters["ownership"]]
            
            if "available_only" in filters and filters["available_only"]:
                results = [p for p in results if p["ownership"] == "UC"]
        
        return results
    
    def get_by_name(self, name: str) -> Optional[Dict]:
        """Get player by exact name (case insensitive)"""
        for player_name, player_data in self.players.items():
            if player_name.lower() == name.lower():
                return player_data
        return None
    
    def get_by_position(self, position: str) -> List[Dict]:
        """Get all players at a position"""
        player_names = self.players_by_position.get(position, [])
        return [self.players[name] for name in player_names]
    
    def resolve_name(self, input_name: str, cutoff: float = 0.7) -> Optional[str]:
        """
        Resolve input to canonical player name using fuzzy matching.
        
        Args:
            input_name: Name as typed by user
            cutoff: Similarity threshold (0.0-1.0)
            
        Returns:
            Canonical player name or None if no match
        """
        # Try exact match first
        exact = self.get_by_name(input_name)
        if exact:
            return exact["name"]
        
        # Try fuzzy match
        all_names = list(self.players.keys())
        matches = get_close_matches(input_name, all_names, n=1, cutoff=cutoff)
        
        return matches[0] if matches else None
    
    def apply_draft_picks(self, picks: List[Dict]) -> None:
        """
        Update ownership based on draft picks.
        Called after draft to mark players as drafted.
        
        Args:
            picks: List of pick records from DraftManager
        """
        for pick in picks:
            player_name = pick.get("player", "")
            team = pick.get("team", "")
            
            # Find player
            canonical = self.resolve_name(player_name)
            if canonical and canonical in self.players:
                # Mark as PC (Purchased Contract) and set new owner
                self.players[canonical]["ownership"] = "PC"
                self.players[canonical]["owner"] = team
    
    def get_available_count(self) -> int:
        """Get count of available (UC) players"""
        return sum(1 for p in self.players.values() if p["ownership"] == "UC")
    
    def get_owned_count(self) -> int:
        """Get count of owned (PC/FC/DC) players"""
        return sum(1 for p in self.players.values() if p["ownership"] in ["PC", "FC", "DC"])


# Testing
if __name__ == "__main__":
    print("🧪 Testing ProspectDatabase")
    print("=" * 50)
    
    db = ProspectDatabase(draft_type="prospect")
    
    print(f"\n📊 Database Stats:")
    print(f"   Total players: {len(db.players)}")
    print(f"   Available (UC): {db.get_available_count()}")
    print(f"   Owned (PC/FC/DC): {db.get_owned_count()}")
    
    print(f"\n📋 Sample Players:")
    for i, (name, data) in enumerate(list(db.players.items())[:5]):
        print(f"   {i+1}. {name}")
        print(f"      Position: {data['position']}")
        print(f"      Team: {data['team']}")
        print(f"      Ownership: {data['ownership']}")
        if data['owner']:
            print(f"      Owner: {data['owner']}")
    
    print(f"\n🔍 Test Search:")
    results = db.search("Chourio")
    print(f"   Search 'Chourio': {len(results)} results")
    for r in results:
        print(f"      - {r['name']} ({r['position']}, {r['team']})")
    
    print(f"\n📍 Test Position Filter:")
    catchers = db.get_by_position("C")
    print(f"   Catchers: {len(catchers)} found")
    for c in catchers[:3]:
        print(f"      - {c['name']}")
    
    print(f"\n✅ ProspectDatabase tests complete!")

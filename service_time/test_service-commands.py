# service_time/test_service_commands.py - Test service commands locally

import json
import os

STATS_FILE = "data/service_stats.json"

def load_service_data():
    """Load service statistics data"""
    try:
        with open(STATS_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ service_stats.json not found")
        return {}

def test_player_lookup():
    """Test individual player service lookup"""
    stats_data = load_service_data()
    
    if not stats_data:
        print("❌ No service data available")
        return
    
    print(f"📊 Found service data for {len(stats_data)} players")
    
    # Show some sample players
    print("\n🔍 Sample players with alerts:")
    alert_count = 0
    
    for name, data in list(stats_data.items())[:10]:
        mlb = data["mlb_limits_status"]
        fbp = data["fbp_limits_status"]
        
        alerts = []
        for limits in [mlb, fbp]:
            for stat_type, info in limits.items():
                if info["exceeded"]:
                    alerts.append(f"🚨 {stat_type.replace('_', ' ').title()} EXCEEDED")
                elif info["percentage"] >= 90:
                    alerts.append(f"⚠️ {stat_type.replace('_', ' ').title()} at {info['percentage']:.0f}%")
        
        if alerts:
            print(f"\n📋 {name} ({data.get('manager', 'Unknown')}):")
            for alert in alerts:
                print(f"   {alert}")
            alert_count += 1
    
    print(f"\n📊 Found {alert_count} players with alerts in sample")

def test_team_summary():
    """Test team prospect summaries"""
    stats_data = load_service_data()
    
    if not stats_data:
        return
    
    # Group by manager
    by_manager = {}
    for name, data in stats_data.items():
        manager = data.get("manager", "Unknown")
        if manager not in by_manager:
            by_manager[manager] = []
        by_manager[manager].append({
            "name": name,
            "data": data
        })
    
    print(f"\n📊 Team Prospect Summary:")
    for manager, prospects in sorted(by_manager.items()):
        if manager == "Unknown":
            continue
            
        # Count alerts
        alerts = 0
        for prospect in prospects:
            for limits in [prospect["data"]["mlb_limits_status"], prospect["data"]["fbp_limits_status"]]:
                for info in limits.values():
                    if info["exceeded"] or info["percentage"] >= 90:
                        alerts += 1
                        break
        
        print(f"   {manager}: {len(prospects)} prospects, {alerts} with alerts")

def show_critical_alerts():
    """Show the most critical service day alerts"""
    stats_data = load_service_data()
    
    critical = []
    warning = []
    
    for name, data in stats_data.items():
        has_critical = False
        has_warning = False
        
        for limits in [data["mlb_limits_status"], data["fbp_limits_status"]]:
            for stat_type, info in limits.items():
                if info["exceeded"]:
                    has_critical = True
                elif info["percentage"] >= 90:
                    has_warning = True
        
        if has_critical:
            critical.append(name)
        elif has_warning:
            warning.append(name)
    
    print(f"\n🚨 Critical Alerts Summary:")
    print(f"   🚨 {len(critical)} players with EXCEEDED limits")
    print(f"   ⚠️ {len(warning)} players at 90%+ limits")
    
    if critical:
        print(f"\n🚨 Players with exceeded limits:")
        for name in critical[:10]:  # Show first 10
            manager = stats_data[name].get("manager", "Unknown")
            print(f"   • {name} ({manager})")

def main():
    print("🔍 Service Days Command Tester")
    print("=" * 50)
    
    # Test if data exists
    if not os.path.exists(STATS_FILE):
        print("❌ No service_stats.json found")
        print("   Run: python3 service_time/service_days_tracker.py")
        return
    
    test_player_lookup()
    test_team_summary()
    show_critical_alerts()
    
    print("\n" + "=" * 50)
    print("📋 Discord Commands Available:")
    print("   /service <player_name> - Individual player report")
    print("   /prospects [team] - Team prospects summary")  
    print("   /alerts - League-wide alerts")
    
    print("\n🎯 To enable in Discord:")
    print("   1. Add 'await bot.load_extension(\"commands.service\")' to bot.py")
    print("   2. Restart your bot")
    print("   3. Test with /service command")

if __name__ == "__main__":
    main()
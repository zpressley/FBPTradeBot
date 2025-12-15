# service_time/test_sheets_access.py - Test and fix Google Sheets permissions

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

SHEET_KEY = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"

def test_sheets_access():
    """Test Google Sheets access and permissions"""
    
    print("🔍 Testing Google Sheets Access...")
    
    try:
        # Load credentials
        with open("google_creds.json", 'r') as f:
            creds_data = json.load(f)
        
        service_email = creds_data.get("client_email")
        print(f"📧 Service account email: {service_email}")
        
        # Set up client
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
        client = gspread.authorize(creds)
        
        # Try to open the main sheet
        print(f"📊 Attempting to open sheet: {SHEET_KEY}")
        sheet = client.open_by_key(SHEET_KEY)
        print(f"✅ Successfully opened sheet: {sheet.title}")
        
        # List existing worksheets
        worksheets = sheet.worksheets()
        print(f"📋 Found {len(worksheets)} worksheets:")
        for ws in worksheets:
            print(f"   - {ws.title} ({ws.row_count} rows, {ws.col_count} cols)")
        
        # Test creating a new worksheet
        test_tab_name = "Service Days Test"
        
        try:
            # Try to get existing test tab
            test_ws = sheet.worksheet(test_tab_name)
            print(f"📄 Found existing test worksheet: {test_tab_name}")
            
            # Try to write to it
            test_ws.update('A1', 'Test Write Access')
            print("✅ Write access confirmed")
            
            # Clean up test
            test_ws.update('A1', '')
            
        except gspread.exceptions.WorksheetNotFound:
            print(f"📝 Creating test worksheet: {test_tab_name}")
            test_ws = sheet.add_worksheet(title=test_tab_name, rows=10, cols=5)
            
            # Test writing
            test_ws.update('A1', 'Test Write Access')
            print("✅ New worksheet created and write access confirmed")
            
            # Clean up - delete test worksheet
            sheet.del_worksheet(test_ws)
            print("🗑️ Test worksheet cleaned up")
        
        print("\n🎯 PERMISSION SOLUTION:")
        print(f"The service account ({service_email}) needs to be:")
        print("1. Added as an editor to your Google Sheet")
        print("2. Given permission to create new tabs")
        print("\n📝 TO FIX:")
        print("1. Open your Google Sheet in browser")
        print("2. Click 'Share' button")
        print(f"3. Add this email as Editor: {service_email}")
        print("4. Make sure 'Can edit' permission is selected")
        
        return True
        
    except gspread.exceptions.APIError as e:
        print(f"❌ Google Sheets API Error: {e}")
        
        if "403" in str(e):
            print("\n🔧 PERMISSION ISSUE DETECTED:")
            print("The service account doesn't have access to the sheet.")
            print("\n📝 TO FIX:")
            print("1. Open your Google Sheet in browser")  
            print(f"2. Click 'Share' → Add: {service_email}")
            print("3. Set permission to 'Editor'")
            print("4. Re-run this test")
        
        return False
        
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def create_service_tracker_tab():
    """Create the service tracker tab with proper permissions"""
    
    print("\n🔄 Creating Service Days Tracker tab...")
    
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
        client = gspread.authorize(creds)
        
        sheet = client.open_by_key(SHEET_KEY)
        
        service_tab = "Service Days Tracker"
        
        # Try to get existing tab or create new
        try:
            worksheet = sheet.worksheet(service_tab)
            print(f"✅ Found existing tab: {service_tab}")
            return True
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=service_tab, rows=1000, cols=25)
            print(f"✅ Created new tab: {service_tab}")
            
            # Add basic headers
            headers = [
                "Player Name", "Manager", "MLB ID", "Last Updated",
                "AB (Current)", "AB MLB %", "AB FBP %", 
                "IP (Current)", "IP MLB %", "IP FBP %",
                "Days (Current)", "Days MLB %",
                "Appearances", "App FBP %", "Status"
            ]
            
            worksheet.update('A1:O1', [headers])
            print("✅ Added headers to new tab")
            
            return True
            
    except Exception as e:
        print(f"❌ Error creating tab: {e}")
        return False

if __name__ == "__main__":
    print("🔍 Google Sheets Permission Tester")
    print("=" * 50)
    
    if test_sheets_access():
        create_service_tracker_tab()
    
    print("\n" + "=" * 50)
    print("📋 NEXT STEPS:")
    print("1. Fix the permission issue above")
    print("2. Re-run: python3 service_time/service_days_tracker.py")
    print("3. Check your Google Sheet for the new 'Service Days Tracker' tab")
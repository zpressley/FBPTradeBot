# quick_sheets_test.py - Quick test for Google Sheets permissions

import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials

def test_sheets_permissions():
    """Quick test of Google Sheets access"""
    
    print("🔍 Testing Google Sheets Permissions...")
    
    try:
        # Load credentials to get service account email
        with open("google_creds.json", 'r') as f:
            creds_data = json.load(f)
        
        service_email = creds_data.get("client_email")
        print(f"📧 Service Account Email: {service_email}")
        
        # Try to access the sheet
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
        client = gspread.authorize(creds)
        
        sheet_key = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"
        sheet = client.open_by_key(sheet_key)
        
        print(f"✅ Successfully accessed sheet: {sheet.title}")
        
        # List worksheets
        worksheets = sheet.worksheets()
        print(f"📋 Found {len(worksheets)} worksheets:")
        for ws in worksheets:
            print(f"   - {ws.title}")
        
        # Try to create test worksheet
        try:
            test_ws = sheet.add_worksheet(title="Service Days Tracker", rows=100, cols=20)
            print("✅ Successfully created 'Service Days Tracker' worksheet!")
            print("🎉 Google Sheets permissions are working correctly!")
            return True
            
        except Exception as e:
            if "already exists" in str(e).lower():
                print("ℹ️ 'Service Days Tracker' worksheet already exists")
                return True
            else:
                print(f"❌ Error creating worksheet: {e}")
                return False
        
    except Exception as e:
        print(f"❌ Google Sheets Error: {e}")
        
        if "403" in str(e) or "permission" in str(e).lower():
            print(f"\n🔧 PERMISSION FIX NEEDED:")
            print(f"1. Open your Google Sheet: https://docs.google.com/spreadsheets/d/13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA")
            print(f"2. Click 'Share' button")
            print(f"3. Add this email as Editor: {service_email}")
            print(f"4. Make sure permission is set to 'Editor'")
            print(f"5. Re-run: python3 service_time/service_days_tracker.py")
        
        return False

if __name__ == "__main__":
    test_sheets_permissions()
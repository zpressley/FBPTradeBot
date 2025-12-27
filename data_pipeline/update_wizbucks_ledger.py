#!/usr/bin/env python3
"""
Extract WizBucks transactions from Google Sheets ledger
Generates wizbucks_transactions.json for the website
"""

import gspread
import json
import os
from oauth2client.service_account import ServiceAccountCredentials

# Google Sheet settings
SHEET_KEY = "172eaArOcLoViepVh14sW3JLjyDGB3yfFVxVjIG9kEak"  # FBP HUB 2.0
TAB_NAME = "WB Ledger"
OUTPUT_FILE = "data/wizbucks_transactions.json"

def get_ledger_transactions():
    """Extract all transactions from WB Ledger sheet"""
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_KEY).worksheet(TAB_NAME)
    
    # Get all data
    raw_data = sheet.get_all_values()
    
    # Headers should be in row with "Action", "Note", "Date", etc.
    # Find header row
    header_row = None
    for i, row in enumerate(raw_data):
        if 'Action' in row and 'Note' in row and 'Date' in row:
            header_row = i
            break
    
    if header_row is None:
        print("❌ Could not find header row")
        return []
    
    headers = raw_data[header_row]
    data_rows = raw_data[header_row + 1:]
    
    # Find column indices
    try:
        action_idx = headers.index('Action')
        note_idx = headers.index('Note')
        date_idx = headers.index('Date')
        credit_idx = headers.index('+')
        debit_idx = headers.index('-')
        manager_idx = headers.index('Manager')
        balance_idx = headers.index('Balance')
    except ValueError as e:
        print(f"❌ Missing expected column: {e}")
        return []
    
    transactions = []
    transaction_id = 1
    
    for row in data_rows:
        # Skip empty rows
        if len(row) <= max(action_idx, note_idx, date_idx, manager_idx):
            continue
        
        action = row[action_idx].strip()
        note = row[note_idx].strip()
        date = row[date_idx].strip()
        manager = row[manager_idx].strip()
        
        # Skip if no action or manager
        if not action or not manager:
            continue
        
        # Parse amounts
        try:
            credit_str = row[credit_idx].strip().replace('$', '').replace(',', '')
            debit_str = row[debit_idx].strip().replace('$', '').replace(',', '')
            balance_str = row[balance_idx].strip().replace('$', '').replace(',', '')
            
            credit = int(credit_str) if credit_str else 0
            debit = int(debit_str) if debit_str else 0
            balance = int(balance_str) if balance_str else 0
        except (ValueError, IndexError):
            print(f"⚠️ Skipping row with invalid amounts: {row}")
            continue
        
        transactions.append({
            "id": transaction_id,
            "action": action,
            "note": note,
            "date": date,
            "credit": credit,
            "debit": debit,
            "manager": manager,
            "balance": balance
        })
        
        transaction_id += 1
    
    return transactions

def save_to_json(transactions, filename=OUTPUT_FILE):
    """Save transactions to JSON file"""
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    
    with open(filename, 'w') as f:
        json.dump(transactions, f, indent=2)
    
    print(f"✅ Saved {len(transactions)} transactions to {filename}")

if __name__ == "__main__":
    print("💰 Extracting WizBucks transactions from Google Sheets...")
    
    transactions = get_ledger_transactions()
    
    if transactions:
        save_to_json(transactions)
        
        # Show summary
        print(f"\n📊 Transaction Summary:")
        print(f"   Total transactions: {len(transactions)}")
        
        # Count by action type
        action_counts = {}
        for t in transactions:
            action = t['action']
            action_counts[action] = action_counts.get(action, 0) + 1
        
        print(f"\n   By action type:")
        for action, count in sorted(action_counts.items(), key=lambda x: -x[1]):
            print(f"   - {action}: {count}")
        
        # Latest transaction
        if transactions:
            latest = transactions[-1]
            print(f"\n   Latest: {latest['date']} - {latest['action']} - {latest['note']}")
    else:
        print("❌ No transactions found")

#!/usr/bin/env python3
"""
Debug script to inspect Player Database.csv structure
"""

import csv

csv_path = "data/Player Database.csv"

print("🔍 Inspecting Player Database.csv")
print("=" * 70)
print()

try:
    with open(csv_path, 'r', encoding='utf-8') as f:
        # Read first few lines raw
        print("📄 First 3 raw lines:")
        print("-" * 70)
        f.seek(0)
        for i in range(3):
            line = f.readline()
            print(f"Line {i}: {line[:200]}")  # First 200 chars
        print()
        
        # Reset and use CSV reader
        f.seek(0)
        reader = csv.reader(f)
        
        # Get headers
        headers = next(reader)
        print(f"📋 Total columns: {len(headers)}")
        print()
        print("Column headers:")
        for i, header in enumerate(headers[:15]):  # First 15 columns
            print(f"  Column {i}: '{header}'")
        print()
        
        # Try DictReader
        f.seek(0)
        dict_reader = csv.DictReader(f)
        
        print("📊 DictReader fieldnames:")
        print(f"  {dict_reader.fieldnames[:10]}")
        print()
        
        # Read first data row
        first_row = next(dict_reader)
        print("🔬 First data row:")
        print(f"  UPID: '{first_row.get('UPID', 'NOT FOUND')}'")
        print(f"  Player Name: '{first_row.get('Player Name', 'NOT FOUND')}'")
        print(f"  Rank/ADP: '{first_row.get('Rank/ADP', 'NOT FOUND')}'")
        print()
        
        # Count rows with ranks
        f.seek(0)
        dict_reader = csv.DictReader(f)
        
        total_rows = 0
        rows_with_rank = 0
        rows_with_upid = 0
        rows_with_both = 0
        
        sample_ranks = []
        
        for row in dict_reader:
            total_rows += 1
            
            upid = row.get('UPID', '').strip()
            rank = row.get('Rank/ADP', '').strip()
            
            if upid:
                rows_with_upid += 1
            if rank:
                rows_with_rank += 1
            if upid and rank:
                rows_with_both += 1
                if len(sample_ranks) < 5:
                    sample_ranks.append({
                        'upid': upid,
                        'name': row.get('Player Name', ''),
                        'rank': rank
                    })
        
        print(f"📊 CSV Statistics:")
        print(f"  Total rows: {total_rows}")
        print(f"  Rows with UPID: {rows_with_upid}")
        print(f"  Rows with Rank/ADP: {rows_with_rank}")
        print(f"  Rows with BOTH: {rows_with_both}")
        print()
        
        if sample_ranks:
            print(f"✅ Sample rows with both UPID and Rank:")
            for sample in sample_ranks:
                print(f"  UPID: {sample['upid']}, Name: {sample['name']}, Rank: {sample['rank']}")
        
except FileNotFoundError:
    print(f"❌ File not found: {csv_path}")
    print()
    print("Try:")
    print(f"  ls -la data/Player*")
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
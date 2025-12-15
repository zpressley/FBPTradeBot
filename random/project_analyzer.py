# project_analyzer.py - Analyze FBP project files for cleanup

import os
import json
from datetime import datetime
from pathlib import Path

class ProjectAnalyzer:
    def __init__(self, project_root="."):
        self.project_root = Path(project_root)
        self.file_analysis = {}
        self.directories = {}
        
    def analyze_project(self):
        """Analyze all files in the project"""
        print("🔍 Analyzing FBP Trade Bot project structure...")
        
        # Core categories
        categories = {
            "CRITICAL_CORE": [],
            "DISCORD_BOT": [],
            "SERVICE_TIME": [],
            "DATA_PIPELINE": [],
            "CONFIG_FILES": [],
            "DATA_FILES": [],
            "DOCUMENTATION": [],
            "LEGACY_DUPLICATE": [],
            "UNKNOWN": []
        }
        
        # Walk through all files
        for root, dirs, files in os.walk(self.project_root):
            # Skip hidden directories and __pycache__
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
            
            rel_root = os.path.relpath(root, self.project_root)
            if rel_root == '.':
                rel_root = 'ROOT'
            
            for file in files:
                if file.startswith('.') or file.endswith('.pyc'):
                    continue
                
                filepath = os.path.join(root, file)
                rel_path = os.path.relpath(filepath, self.project_root)
                
                # Analyze file
                file_info = self.analyze_file(filepath, rel_path)
                category = self.categorize_file(rel_path, file)
                
                categories[category].append({
                    "path": rel_path,
                    "size": file_info["size"],
                    "modified": file_info["modified"],
                    "description": file_info["description"]
                })
        
        return categories
    
    def analyze_file(self, filepath, rel_path):
        """Analyze individual file"""
        try:
            stat = os.stat(filepath)
            size = stat.st_size
            modified = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
            
            # Try to determine file purpose
            description = self.describe_file(filepath, rel_path)
            
            return {
                "size": size,
                "modified": modified,
                "description": description
            }
        except Exception as e:
            return {
                "size": 0,
                "modified": "Unknown",
                "description": f"Error reading file: {e}"
            }
    
    def describe_file(self, filepath, rel_path):
        """Describe file purpose based on content/name"""
        filename = os.path.basename(filepath)
        
        # Special files
        if filename == "bot.py":
            return "Main Discord bot entry point"
        elif filename == "health.py":
            return "Bot health check with FastAPI"
        elif filename == "requirements.txt":
            return "Python dependencies"
        elif filename == "google_creds.json":
            return "Google Sheets service account credentials"
        elif filename == "token.json":
            return "Yahoo Fantasy API token"
        elif filename.endswith("_context.md"):
            return "Context documentation for development"
        
        # Try to read first few lines for Python files
        if filename.endswith('.py'):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    lines = f.readlines()[:10]
                    
                # Look for docstrings or comments
                for line in lines:
                    line = line.strip()
                    if line.startswith('"""') or line.startswith("'''"):
                        return line.replace('"""', '').replace("'''", '').strip()
                    elif line.startswith('#') and len(line) > 5:
                        return line[1:].strip()
                
                # Look for class/function names
                for line in lines:
                    if line.strip().startswith('class '):
                        class_name = line.strip().split()[1].split('(')[0]
                        return f"Class: {class_name}"
                    elif line.strip().startswith('def ') and 'main' in line:
                        return "Main script"
                
                return "Python script"
                
            except:
                return "Python file (couldn't read)"
        
        # JSON files
        elif filename.endswith('.json'):
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        keys = list(data.keys())[:3]
                        return f"JSON data: {', '.join(keys)}"
                    elif isinstance(data, list) and data:
                        return f"JSON array with {len(data)} items"
                return "JSON file"
            except:
                return "JSON file (couldn't parse)"
        
        # Other files
        elif filename.endswith('.md'):
            return "Markdown documentation"
        elif filename.endswith('.pdf'):
            return "PDF document"
        elif filename.endswith('.xlsx'):
            return "Excel spreadsheet"
        elif filename.endswith('.yml') or filename.endswith('.yaml'):
            return "YAML configuration"
        
        return "Unknown file type"
    
    def categorize_file(self, rel_path, filename):
        """Categorize file based on path and name"""
        path_lower = rel_path.lower()
        file_lower = filename.lower()
        
        # Critical core files
        if filename in ["bot.py", "health.py", "requirements.txt", "google_creds.json", "token.json"]:
            return "CRITICAL_CORE"
        
        # Discord bot commands
        if "commands/" in path_lower or filename in ["trade_logic.py", "utils.py", "lookup.py"]:
            return "DISCORD_BOT"
        
        # Service time tracking
        if "service_time/" in path_lower or "service" in file_lower:
            return "SERVICE_TIME"
        
        # Data pipeline
        if "data_pipeline/" in path_lower:
            return "DATA_PIPELINE"
        
        # Configuration
        if filename in [".env", ".gitignore"] or file_lower.endswith(('.yml', '.yaml', '.toml')):
            return "CONFIG_FILES"
        
        # Data files
        if path_lower.startswith('data/') or file_lower.endswith('.json'):
            return "DATA_FILES"
        
        # Documentation
        if file_lower.endswith(('.md', '.txt', '.pdf')) or "readme" in file_lower or "context" in file_lower:
            return "DOCUMENTATION"
        
        # Legacy/duplicate detection
        legacy_indicators = [
            "old", "backup", "test", "temp", "draft", "copy", "unused", 
            "legacy", "archive", "bak", "_old", "_backup"
        ]
        
        if any(indicator in path_lower or indicator in file_lower for indicator in legacy_indicators):
            return "LEGACY_DUPLICATE"
        
        # Check for potential duplicates
        if self.might_be_duplicate(rel_path, filename):
            return "LEGACY_DUPLICATE"
        
        return "UNKNOWN"
    
    def might_be_duplicate(self, rel_path, filename):
        """Check if file might be a duplicate"""
        # Files with similar names in different locations
        duplicates = [
            ("build_mlb_id_cache.py", "enhanced_id_mapper.py"),
            ("update_yahoo_players.py", "data_pipeline/update_yahoo_players.py"),
            ("track_roster_status.py", "enhanced_service_tracker.py"),
            ("flagged_service_tracker.py", "enhanced_service_tracker.py")
        ]
        
        for dup_pair in duplicates:
            if filename in dup_pair:
                return True
        
        return False
    
    def generate_report(self, categories):
        """Generate cleanup report"""
        print("\n" + "="*80)
        print("📋 FBP PROJECT CLEANUP ANALYSIS")
        print("="*80)
        
        total_files = sum(len(files) for files in categories.values())
        total_size = sum(sum(f["size"] for f in files) for files in categories.values())
        
        print(f"📊 Total Files: {total_files}")
        print(f"📦 Total Size: {total_size / 1024:.1f} KB")
        
        # Recommendations
        recommendations = {
            "KEEP": ["CRITICAL_CORE", "DISCORD_BOT", "SERVICE_TIME", "CONFIG_FILES"],
            "REVIEW": ["DATA_PIPELINE", "DATA_FILES", "DOCUMENTATION"],
            "MOVE_TO_BACKUP": ["LEGACY_DUPLICATE", "UNKNOWN"]
        }
        
        for action, category_list in recommendations.items():
            print(f"\n🎯 {action}:")
            
            for category in category_list:
                files = categories.get(category, [])
                if files:
                    print(f"\n  📁 {category} ({len(files)} files):")
                    
                    for file_info in sorted(files, key=lambda x: x["path"]):
                        size_kb = file_info["size"] / 1024
                        status = "🟢" if action == "KEEP" else "🟡" if action == "REVIEW" else "🔴"
                        print(f"    {status} {file_info['path']}")
                        print(f"        Size: {size_kb:.1f}KB | Modified: {file_info['modified']}")
                        print(f"        {file_info['description']}")
        
        # Generate move commands
        print(f"\n" + "="*80)
        print("🚀 CLEANUP COMMANDS")
        print("="*80)
        
        backup_files = []
        for category in ["LEGACY_DUPLICATE", "UNKNOWN"]:
            backup_files.extend(categories.get(category, []))
        
        if backup_files:
            print("# Create backup directory")
            print("mkdir -p backup")
            print()
            print("# Move files to backup:")
            
            for file_info in backup_files:
                path = file_info["path"]
                # Create directory structure in backup
                backup_dir = os.path.dirname(f"backup/{path}")
                if backup_dir and backup_dir != "backup":
                    print(f"mkdir -p '{backup_dir}'")
                print(f"mv '{path}' 'backup/{path}'")
        
        print("\n# After moving files, you can remove empty directories:")
        print("find . -type d -empty -delete")
        
        return categories
    
    def generate_file_tree(self, categories):
        """Generate a clean file tree of what should remain"""
        print(f"\n" + "="*80)
        print("🌳 RECOMMENDED PROJECT STRUCTURE (After Cleanup)")
        print("="*80)
        
        keep_files = []
        for category in ["CRITICAL_CORE", "DISCORD_BOT", "SERVICE_TIME", "CONFIG_FILES"]:
            keep_files.extend(categories.get(category, []))
        
        # Group by directory
        structure = {}
        for file_info in keep_files:
            path = file_info["path"]
            dir_path = os.path.dirname(path) or "ROOT"
            filename = os.path.basename(path)
            
            if dir_path not in structure:
                structure[dir_path] = []
            structure[dir_path].append(filename)
        
        # Print structure
        for dir_path in sorted(structure.keys()):
            if dir_path == "ROOT":
                print("📁 fbp-trade-bot/")
            else:
                print(f"📁 {dir_path}/")
            
            for filename in sorted(structure[dir_path]):
                print(f"    📄 {filename}")

def main():
    analyzer = ProjectAnalyzer()
    categories = analyzer.analyze_project()
    analyzer.generate_report(categories)
    analyzer.generate_file_tree(categories)
    
    print(f"\n✅ Analysis complete! Review the recommendations above.")
    print(f"🎯 Focus on the MOVE_TO_BACKUP section for cleanup commands.")

if __name__ == "__main__":
    main()
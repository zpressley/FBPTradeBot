#!/bin/bash
#
# Quick commit script for FBP Trade Bot
# Usage: ./commit.sh
#

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}📦 FBP Trade Bot - Quick Commit${NC}"
echo "================================"
echo ""

# Check if there are changes
if [[ -z $(git status -s) ]]; then
    echo -e "${YELLOW}⚠️  No changes to commit${NC}"
    exit 0
fi

# Show what will be committed
echo -e "${BLUE}📋 Files changed:${NC}"
git status -s
echo ""

# Ask for confirmation
read -p "$(echo -e ${YELLOW}Commit all these changes? [y/N]:${NC} )" -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${RED}❌ Commit cancelled${NC}"
    exit 1
fi

# Get commit message
echo ""
echo -e "${BLUE}✍️  Enter commit message:${NC}"
read -r commit_message

if [[ -z "$commit_message" ]]; then
    echo -e "${RED}❌ Commit message cannot be empty${NC}"
    exit 1
fi

# Add all changes
echo ""
echo -e "${GREEN}📦 Adding all changes...${NC}"
git add .

# Commit
echo -e "${GREEN}💾 Committing...${NC}"
git commit -m "$commit_message"

# Ask about pushing
echo ""
read -p "$(echo -e ${YELLOW}Push to GitHub? [y/N]:${NC} )" -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${GREEN}🚀 Pushing to GitHub...${NC}"
    git push origin main
    echo ""
    echo -e "${GREEN}✅ Done! Changes committed and pushed.${NC}"
else
    echo ""
    echo -e "${YELLOW}✅ Done! Changes committed locally.${NC}"
    echo -e "${YELLOW}💡 Run 'git push origin main' to push when ready.${NC}"
fi
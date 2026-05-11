#!/bin/bash
# Quick test to check PGA Tour API for The American Express results

echo "====================================="
echo "Testing PGA Tour API - The American Express (Jan 22-25, 2026)"
echo "====================================="
echo ""

# Fetch the data
echo "Fetching data from ESPN API..."
response=$(curl -s "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard?dates=20260125")

# Save to file for inspection
echo "$response" > /tmp/american_express.json
echo "Full response saved to: /tmp/american_express.json"
echo ""

# Check if we got data
if echo "$response" | grep -q "The American Express"; then
    echo "✓ Successfully fetched The American Express tournament data"
    echo ""

    # Try to extract basic info
    echo "Tournament Name:"
    echo "$response" | grep -o '"name":"The American Express"' | head -1
    echo ""

    echo "Status:"
    echo "$response" | grep -o '"description":"[^"]*"' | head -3
    echo ""

    echo "You can inspect the full JSON at: /tmp/american_express.json"
    echo ""
    echo "To see competitors, you can use:"
    echo "  cat /tmp/american_express.json | jq '.events[0].competitions[0].competitors[0:5]'"
    echo ""

else
    echo "✗ Failed to fetch tournament data"
    echo "Response (first 500 chars):"
    echo "$response" | head -c 500
fi

echo ""
echo "====================================="
echo "Test complete"
echo "====================================="

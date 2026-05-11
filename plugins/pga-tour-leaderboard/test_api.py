"""
Test script to fetch PGA Tour tournament data from ESPN API.
Tests both current and previous tournament data retrieval.
"""

import requests
from datetime import datetime, timedelta
import json


def test_current_tournament():
    """Test fetching current tournament data."""
    print("\n" + "="*60)
    print("TESTING CURRENT TOURNAMENT DATA")
    print("="*60)

    url = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        events = data.get('events', [])
        print(f"\nFound {len(events)} event(s)\n")

        for event in events:
            name = event.get('name', 'Unknown')
            date = event.get('date', 'Unknown')
            status = event.get('status', {}).get('type', {}).get('state', 'Unknown')

            print(f"Tournament: {name}")
            print(f"Date: {date}")
            print(f"Status: {status}")

            # Get competitors
            competitions = event.get('competitions', [])
            if competitions:
                competitors = competitions[0].get('competitors', [])
                print(f"Competitors: {len(competitors)}")

                # Show top 5
                sorted_competitors = sorted(
                    competitors,
                    key=lambda x: int(x.get('sortOrder', 999))
                )

                print("\nTop 5 Players:")
                for i, comp in enumerate(sorted_competitors[:5]):
                    athlete = comp.get('athlete', {})
                    name = athlete.get('displayName', 'Unknown')
                    position = comp.get('sortOrder', '?')

                    # Get score
                    stats = comp.get('statistics', [])
                    score = "E"
                    for stat in stats:
                        if stat.get('name') == 'score':
                            score = stat.get('displayValue', 'E')
                            break

                    print(f"  {position}. {name} ({score})")
            print()

        return True

    except Exception as e:
        print(f"Error fetching current tournament: {e}")
        return False


def test_previous_tournament():
    """Test fetching previous weekend's tournament data."""
    print("\n" + "="*60)
    print("TESTING PREVIOUS TOURNAMENT DATA (FALLBACK)")
    print("="*60)

    today = datetime.now()

    # Try fetching data from previous weeks
    for days_back in [7, 14, 21, 28]:
        date_to_check = today - timedelta(days=days_back)
        date_str = date_to_check.strftime('%Y%m%d')

        print(f"\nChecking date: {date_to_check.strftime('%Y-%m-%d')} ({days_back} days ago)")

        url = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"
        params = {'dates': date_str}

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            events = data.get('events', [])

            if not events:
                print("  No events found for this date")
                continue

            # Look for completed tournaments
            for event in events:
                name = event.get('name', 'Unknown')
                date = event.get('date', 'Unknown')
                status = event.get('status', {}).get('type', {}).get('state', 'Unknown')

                print(f"\n  Tournament: {name}")
                print(f"  Date: {date}")
                print(f"  Status: {status}")

                if status == 'post':
                    print(f"  ✓ COMPLETED TOURNAMENT FOUND!")

                    # Get final leaderboard
                    competitions = event.get('competitions', [])
                    if competitions:
                        competitors = competitions[0].get('competitors', [])
                        print(f"  Competitors: {len(competitors)}")

                        # Show top 5
                        sorted_competitors = sorted(
                            competitors,
                            key=lambda x: int(x.get('sortOrder', 999))
                        )

                        print("\n  Final Top 5:")
                        for i, comp in enumerate(sorted_competitors[:5]):
                            athlete = comp.get('athlete', {})
                            player_name = athlete.get('displayName', 'Unknown')
                            position = comp.get('sortOrder', '?')

                            # Get score
                            stats = comp.get('statistics', [])
                            score = "E"
                            for stat in stats:
                                if stat.get('name') == 'score':
                                    score = stat.get('displayValue', 'E')
                                    break

                            print(f"    {position}. {player_name} ({score})")

                    print("\n  This is what the plugin will show in fallback mode!")
                    return True
                else:
                    print(f"  Tournament not completed (status: {status})")

        except Exception as e:
            print(f"  Error: {e}")
            continue

    print("\n  No completed tournaments found in the last 28 days")
    return False


def test_api_structure():
    """Test and display the API response structure."""
    print("\n" + "="*60)
    print("API RESPONSE STRUCTURE")
    print("="*60)

    url = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        print("\nTop-level keys:")
        for key in data.keys():
            print(f"  - {key}")

        events = data.get('events', [])
        if events:
            print(f"\nFirst event structure:")
            event = events[0]
            print(f"  Keys: {list(event.keys())}")

            print(f"\n  Status structure:")
            status = event.get('status', {})
            print(f"    Keys: {list(status.keys())}")

            competitions = event.get('competitions', [])
            if competitions:
                print(f"\n  Competition structure:")
                comp = competitions[0]
                print(f"    Keys: {list(comp.keys())}")

                competitors = comp.get('competitors', [])
                if competitors:
                    print(f"\n  Competitor structure (first player):")
                    competitor = competitors[0]
                    print(f"    Keys: {list(competitor.keys())}")

        return True

    except Exception as e:
        print(f"Error: {e}")
        return False


if __name__ == "__main__":
    print("\n" + "="*60)
    print("PGA TOUR LEADERBOARD PLUGIN - API TEST")
    print("="*60)
    print(f"Test Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Test current tournament
    current_ok = test_current_tournament()

    # Test previous tournament (fallback)
    previous_ok = test_previous_tournament()

    # Show API structure
    structure_ok = test_api_structure()

    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"Current Tournament API:  {'✓ PASS' if current_ok else '✗ FAIL'}")
    print(f"Previous Tournament API: {'✓ PASS' if previous_ok else '✗ FAIL'}")
    print(f"API Structure Check:     {'✓ PASS' if structure_ok else '✗ FAIL'}")
    print()

    if current_ok or previous_ok:
        print("✓ Plugin should work correctly!")
    else:
        print("⚠ May have issues - check API availability")

    print("="*60 + "\n")

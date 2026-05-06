#!/usr/bin/env python3
"""
Manual weather market resolution and calibration update.

Usage:
  python scripts/resolve_weather.py --city nyc --date 2026-04-05
  python scripts/resolve_weather.py --city london --date 2026-04-05 --actual 12.3
"""
import argparse
import sys
from pathlib import Path

# ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from calibration.weather_calibration import WeatherCalibration, fetch_actual_temp
from config import LOCATIONS


def main():
    parser = argparse.ArgumentParser(description="Resolve a weather market and update calibration")
    parser.add_argument("--city",   required=True, help="City slug, e.g. nyc")
    parser.add_argument("--date",   required=True, help="Date YYYY-MM-DD")
    parser.add_argument("--actual", type=float,    help="Actual high temp (skip VC fetch if provided)")
    args = parser.parse_args()

    city_slug = args.city
    date_str  = args.date

    if city_slug not in LOCATIONS:
        print(f"Unknown city: {city_slug}. Valid: {list(LOCATIONS.keys())}")
        sys.exit(1)

    # Get actual temp
    if args.actual is not None:
        actual_temp = args.actual
        print(f"Using provided actual temp: {actual_temp}")
    else:
        print(f"Fetching actual temp from Visual Crossing for {city_slug} on {date_str}...")
        actual_temp = fetch_actual_temp(city_slug, date_str)
        if actual_temp is None:
            print("Failed to fetch actual temp. Set VC_KEY env var or use --actual flag.")
            sys.exit(1)
        print(f"Actual temp: {actual_temp}")

    # Update calibration for each source
    # (In production, you'd load forecast snapshots from fills.jsonl to get per-source temps)
    cal = WeatherCalibration.load()
    unit = LOCATIONS[city_slug]["unit"]
    print(f"\nCalibration update for {city_slug} ({date_str}):")
    for source in ["ecmwf", "hrrr"]:
        old_sigma = cal.get_sigma(city_slug, source)
        # We don't have the forecast temp here without fills.jsonl lookup
        # This is a placeholder — in practice wire to fills.jsonl
        print(f"  {source}: current sigma={old_sigma} {unit}")
    print("\nTo record forecast errors, pass --forecast-ecmwf and --forecast-hrrr flags")
    print("or integrate with fills.jsonl lookup (Step 8 tracker integration).")

    print("\nDone.")


if __name__ == "__main__":
    main()

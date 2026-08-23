# GT7 Career Tracker

Automated Gran Turismo 7 Sport Mode career tracker for Driver Rating, qualifying performance, race results, telemetry, and long-term performance analytics.

## Phase 1: Rating history

The first phase automatically records the configured PSN profile's Driver Rating and Sportsmanship Rating from the public GTSH profile source.

Currently captured:

- Driver Rating code and label
- DR points
- Progress percentage toward the next DR level
- Sportsmanship Rating
- UTC capture timestamp

Snapshots are stored in `data/career.db` (SQLite) and the most recent reading is also written to `data/latest_rating.json`.

The GitHub Actions workflow `.github/workflows/collect-rating.yml` runs every three hours and can also be started manually with `workflow_dispatch`.

## Current PSN

`crazy_rooster74`

The PSN can be overridden locally with the `GT7_PSN_ID` environment variable.

## Run locally

```bash
pip install -r requirements.txt
python src/collect_rating.py
```

## Planned phases

1. Rating history (DR/SR)
2. Sport Mode event and qualifying leaderboard collection
3. Cross-event qualifying performance analytics
4. PS5 UDP telemetry ingestion
5. Race-session reconstruction and racecraft metrics
6. Career dashboard and long-term performance indices

## Project isolation

This repository is independent from `gt7-daily-race-agent`. Existing Daily Race C production code is not modified by this project.

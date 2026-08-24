---
title: "Run first full image-fill pass and record per-store coverage"
status: backlog
priority: P2
project: weightlifting-equipment
type: dev
agent_claimed: null
claimed_at: null
created: 2026-08-23T23:05:00Z
updated: 2026-08-23T23:05:00Z
tags: [images, playwright]
due: null
estimate: medium

# WSJF Value Scoring (Product Owner fills in)
business_value: 8
time_criticality: 5
risk_reduction: 4
wsjf_score: null
po_notes: "One-time bootstrap cost; validates the fill pipeline end-to-end before dashboard work"
po_accepted: null
---

# Run first full image-fill pass and record per-store coverage

## Summary
8,503 products in SQLite have `image_url IS NULL`. The `fill_missing_images()` batch-fetch pipeline exists but hasn't run against the full catalog. Execute it, measure per-store success rates, and identify themes that fail.

## Value Proposition
Images are prerequisite for dashboard thumbnails and image-rich deal alerts. This run also stress-tests the fill logic — any bugs surface now rather than silently during daily crons.

## Context
- Trigger: `run_equipment_scrape.py` Pass 2 (runs automatically after scrape+ingest), or standalone via `equipment-db need-images` to inspect state
- Expected behavior: one browser session per store, batches of 20 in-page `fetch()` calls to `/products/{handle}.json`, ~1s between batches
- HTTP stores (LiftingLarge, Get Rx'd, Hookgrip) already have images inline — expect near-zero missing there

## Acceptance Criteria
- [ ] Full fill pass executed against all stores with missing images
- [ ] Per-store coverage table recorded (products / images fetched / % / failures)
- [ ] Stores with <50% coverage investigated — root cause noted (theme? fetch blocked? URL format?)
- [ ] `equipment-db need-images 20` afterwards shows only genuinely unfixable products

## Notes
Rogue Fitness uses DOM extraction (not Shopify preload JSON) so its `/products/{handle}.json` endpoint may not exist — check before assuming failure.

---
title: "Add image_url column to Neon PostgreSQL schema"
status: backlog
priority: P1
project: weightlifting-equipment
type: dev
agent_claimed: null
claimed_at: null
created: 2026-08-23T23:05:00Z
updated: 2026-08-23T23:05:00Z
tags: [database, neon, images]
due: null
estimate: small

# WSJF Value Scoring (Product Owner fills in)
business_value: 8
time_criticality: 9
risk_reduction: 7
wsjf_score: null
po_notes: "Blocking — next cron push to Neon will fail or silently drop image data until this lands"
po_accepted: null
---

# Add image_url column to Neon PostgreSQL schema

## Summary
The `image_url` column was added to local SQLite (Aug 23) but not to the Neon PostgreSQL production schema. The VPS cron pushes every scrape to Neon when `DATABASE_URL` is set — the first push with image data will either error or silently drop images.

## Value Proposition
Unblocks the entire image feature for production. Everything else (dashboard thumbnails, deal alerts with images) depends on Neon having the column.

## Context
- Local migration already applied: `ALTER TABLE products ADD COLUMN image_url TEXT;`
- Runner's inline Neon push (`run_equipment_scrape.py` lines ~72–95) creates tables with `CREATE TABLE IF NOT EXISTS` — existing Neon tables won't gain the column automatically
- `migrate_to_neon.py` may also need updating for fresh installs

## Acceptance Criteria
- [ ] `ALTER TABLE products ADD COLUMN IF NOT EXISTS image_url TEXT;` runs against Neon
- [ ] Runner's CREATE TABLE statement includes `image_url TEXT` for fresh installs
- [ ] A test push confirms image URLs land in Neon rows
- [ ] `migrate_to_neon.py` updated so fresh migrations include the column

## Notes
Do this first — it's blocking. 5-minute fix once connected to Neon.

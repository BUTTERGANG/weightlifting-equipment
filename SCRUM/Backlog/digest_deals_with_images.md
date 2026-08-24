---
title: "Daily digest: hot deals with product images"
status: backlog
priority: P3
project: weightlifting-equipment
type: dev
agent_claimed: null
claimed_at: null
created: 2026-08-23T23:05:00Z
updated: 2026-08-23T23:05:00Z
tags: [cron, deals, discord]
due: null
estimate: medium

# WSJF Value Scoring (Product Owner fills in)
business_value: 6
time_criticality: 2
risk_reduction: 2
wsjf_score: null
po_notes: "Nice-to-have polish; needs deal history (5+ scrapes) and images to be meaningful"
po_accepted: null
---

# Daily digest: hot deals with product images

## Summary
Upgrade the daily 6am cron digest to include product thumbnails for flagged deals (>10% below historical average), making the Discord digest scannable at a glance.

## Value Proposition
A deal alert with a photo of the exact barbell gets acted on; a text-only name match requires opening the dashboard to verify. Images turn notifications into decisions.

## Context
- Deal detection needs 5+ scrape runs of history — currently only 1-2 runs exist, so no deals fire yet. This task becomes testable naturally within a week of daily crons.
- Cron job id: `2170cfe86c69` — prompt reads scrape output and delivers a digest to origin (#development EQUIPMENT thread)
- Discord supports image attachments / embedded image URLs natively

## Acceptance Criteria
- [ ] Digest includes thumbnail for each deal when `image_url` is present
- [ ] Deals without images degrade gracefully (text-only, no broken embeds)
- [ ] Digest stays under Discord embed limits (max ~10 deals with images per message, overflow noted)
- [ ] Verified end-to-end with a real or seeded deal

## Notes
Alternative if embed limits bite: post deals as a single composite image grid generated from thumbnails.

---
title: "Dashboard: product images in table + detail modal"
status: backlog
priority: P3
project: weightlifting-equipment
type: dev
agent_claimed: null
claimed_at: null
created: 2026-08-23T23:05:00Z
updated: 2026-08-23T23:05:00Z
tags: [dashboard, ui, images]
due: null
estimate: medium

# WSJF Value Scoring (Product Owner fills in)
business_value: 7
time_criticality: 3
risk_reduction: 2
wsjf_score: null
po_notes: "High visible value once image data exists; depends on Neon column + first fill pass"
po_accepted: null
---

# Dashboard: product images in table + detail modal

## Summary
Surface `image_url` in the LiftTracker UI: small thumbnails in the main products table and a hero image in the product detail modal (alongside the Chart.js price history).

## Value Proposition
Visual identification makes browsing 8k+ products dramatically faster — users spot the right barbell/belt by sight instead of parsing names. This is the "navigate our own application and easily view items" goal.

## Context
- `GET /api/products` needs `image_url` added to its SELECT
- `GET /api/product/<id>` same
- Frontend: lazy-load thumbnails (`loading="lazy"`), fallback placeholder when NULL
- UX rule: features appear only when data exists — no broken-image icons for products without images

## Acceptance Criteria
- [ ] Products table shows thumbnail column (40–48px, lazy-loaded, only when image exists)
- [ ] Detail modal shows larger hero image next to price history chart
- [ ] Products without images render cleanly — no broken image placeholders
- [ ] Works on both SQLite and Neon backends (column exists in both)

## Notes
Consider a lightbox/zoom on click in the modal if trivial. Don't block table render on image loads.

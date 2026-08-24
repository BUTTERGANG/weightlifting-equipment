---
title: "Sync migrated scrapers from ~/.hermes/scripts into repo"
status: backlog
priority: P2
project: weightlifting-equipment
type: dev
agent_claimed: null
claimed_at: null
created: 2026-08-23T23:05:00Z
updated: 2026-08-23T23:05:00Z
tags: [scraper, playwright, cloudflare]
due: null
estimate: medium

# WSJF Value Scoring (Product Owner fills in)
business_value: 9
time_criticality: 7
risk_reduction: 8
wsjf_score: null
po_notes: "Repo copies are stale — Replit deploys would scrape nothing on the 25 Cloudflare-blocked stores"
po_accepted: null
---

# Sync migrated scrapers from ~/.hermes/scripts into repo

## Summary
The working post-migration scrapers live in `~/.hermes/scripts/` but `scraper/` in the repo still has pre-Cloudflare versions. Aug 23 migrated all 25 Shopify stores to Playwright, fixed 4 domains (pioneerfit.com, cerberus-strength.us, store.weightliftinghouse.com, markbellslingshot.com), and rewrote the image system. None of it is in the repo.

## Value Proposition
The repo is the deployable source of truth. Until synced, Replit deployments run dead code against 429'd HTTP endpoints and get zero products from every Shopify store.

## Context
- Source of truth: `/home/alex/.hermes/scripts/{equipment_scraper.py, browser_scraper.py, equipment_db.py, run_equipment_scrape.py}`
- Repo target: `scraper/` (same filenames minus run_equipment_scrape.py → run_scrape.py orchestrator may need merging)
- AGENTS.md still documents "18 HTTP + 9 Playwright" — reality is 3 HTTP + 25 Playwright
- Skill file has full migration details: `~/.hermes/skills/equipment-price-scraper/SKILL.md`

## Acceptance Criteria
- [ ] All four scraper files copied/adapted into `scraper/`
- [ ] `run_scrape.py` orchestrator merged with new image-fill pass logic
- [ ] AGENTS.md + README.md updated: 3 HTTP / 25 Playwright, venv requirement, domain changes
- [ ] Repo scrape test passes: at least one Playwright store returns products with images

## Notes
Watch for the `shopify_json` parser removal — Rival Steel moved to Playwright preload. Also `extract_product_images()` DOM cascade is retained-but-unused; don't wire it back in.

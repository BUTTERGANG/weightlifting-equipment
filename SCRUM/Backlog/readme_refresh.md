---
title: "README refresh: Cloudflare migration, domains, venv"
status: backlog
priority: P3
project: weightlifting-equipment
type: docs
agent_claimed: null
claimed_at: null
created: 2026-08-23T23:05:00Z
updated: 2026-08-23T23:05:00Z
tags: [docs]
due: null
estimate: small

# WSJF Value Scoring (Product Owner fills in)
business_value: 4
time_criticality: 4
risk_reduction: 5
wsjf_score: null
po_notes: "Cheap but prevents future agents from following stale instructions"
po_accepted: null
---

# README refresh: Cloudflare migration, domains, venv

## Summary
Update README.md and AGENTS.md to reflect the Aug 2026 architecture: all Shopify stores behind Cloudflare 429 (25 Playwright + 3 HTTP), four domain changes, image pipeline, and the venv-Python requirement.

## Value Proposition
Any agent or human picking up the repo gets accurate instructions on the first read instead of debugging why HTTP scraping returns nothing.

## Context
Authoritative reference: `~/.hermes/skills/equipment-price-scraper/SKILL.md` (v1.6.0 changelog section has everything).

Key facts to fold in:
- 3 HTTP stores: LiftingLarge (ASP), Hookgrip (WooCommerce API), Get Rx'd (Magento)
- 25 Playwright stores — every Shopify domain 429s plain requests
- Domains: pioneer.fitness→pioneerfit.com, cerberus-strength.com→cerberus-strength.us, weightliftinghouse.com→store.weightliftinghouse.com
- Images: two-pass system; `image_url` set-once semantics
- Local runs need `/home/alex/.hermes/venv/bin/python3` (Playwright lives there)
- Cron prompt updated to venv path

## Acceptance Criteria
- [ ] README store table matches skill's (3 HTTP / 25 Playwright with methods)
- [ ] Domain-change notes included
- [ ] Image pipeline briefly documented
- [ ] AGENTS.md quick-start uses correct interpreter and reflects new file roles

## Notes
Keep it concise — link to the skill for deep detail rather than duplicating it.

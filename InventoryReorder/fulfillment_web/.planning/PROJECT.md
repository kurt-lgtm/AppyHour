# Command Center — Daily Task Management App

## Overview
A new "Command Center" tab in the existing fulfillment pywebview app that tells the user what to do each day — work and personal tasks, step-by-step guided, with MCP integrations, an embedded Ask Claude chat panel, and compassionate design that reduces anxiety instead of adding it.

## Problem
The user (solo business owner, subscription box company) currently tracks tasks in their head, phone notes, and scattered across 6+ apps (Shopify, Recharge, Gorgias, Gmail, Slack, Google Sheets). Weekly fulfillment cycle has day-specific tasks that get missed. Blockers cause stress because there's no system to monitor and resurface them. Personal tasks fall through the cracks.

## Solution
One tab. Opens first. Shows what to do today, in order, one at a time. Pulls live data from all MCP sources. Has an AI chat panel that can actually execute actions (send emails, pull data, research). Day-of-week recurring tasks auto-populate. Blockers auto-monitor and resurface when cleared. Compassionate design — no red, no guilt, no shame.

## Target Platform
- Pywebview desktop app (existing fulfillment app) — new tab added as first/home tab
- Python backend (Flask/pywebview js_api bridge)
- HTML/CSS/JS frontend (no framework — matches existing app pattern)
- Initially: Windows (current machine, 8GB RAM)
- Eventually: Linux (new 32GB machine)

## Key Constraints
- Must integrate with existing app.py (~250KB) without breaking anything
- Separate CSS/JS files — don't touch existing styles.css (60KB) or app.js (195KB)
- Dark theme: navy (#1a1a2e) base, NO RED anywhere (amber #f5a623 max warning)
- Compassionate design is non-negotiable
- MCP sources: appyhour (Shopify/Recharge/Gorgias/Sheets), Slack, Gmail
- Ask Claude via anthropic SDK, Haiku default, opt-in Deep Think

## Success Criteria
- User opens app → sees today's tasks in <2 seconds
- Day-of-week recurring tasks auto-populate (Tue=cut order, Wed=PO, Fri=weekly review)
- Blocked tasks disappear from view, auto-monitor via MCP, resurface when cleared
- Energy check-in adjusts task order
- Timer tracks actual task duration, system learns over time
- Ask Claude panel can pull live data and send emails
- Personal tasks separate but visible alongside work
- End-of-day summary shows what was accomplished, not what was missed

## Team
- Kurt (owner/operator) — user, product decisions
- Claude — architect, implementer, reviewer

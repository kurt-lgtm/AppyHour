# Phase 7: Ask Claude Chat Panel — Plan

**Status:** COMPLETE (retroactive)
**Created:** 2026-04-06 (retroactive from commit aa28b4f)

## Goal
Embed a context-aware Claude chat panel in the right sidebar so the operator can ask operational questions without leaving the Command Center.

## Deliverables
- Chat input + send button in right sidebar; non-streaming POST to /api/cc/chat
- Haiku default with Deep Think toggle to Sonnet; $2/mo budget cap with per-model cost tracking
- `build_system_prompt()` — includes date, energy, top tasks, inventory alerts
- Action buttons on responses: Copy and Add to tasks
- In-memory chat history capped at 20 messages

## Tasks
1. Add chat panel HTML to right sidebar in `templates/index.html` (textarea + send + deep-think toggle)
2. `POST /api/cc/chat` — accepts {message, deep_think} payload
3. `build_system_prompt()` — assembles: today's date, current energy level, top 3 tasks, any inventory alerts from brief
4. Call Anthropic SDK: `claude-haiku-4-5` default, `claude-sonnet-4-5` when deep_think=true
5. Non-streaming response (not SSE) — full text returned in JSON
6. Budget tracking: `cc_chat_budget` in settings JSON — per-model cost accumulation, monthly reset on new month
7. Hard cap: if monthly spend >= $2.00, return 429-style error with "Budget reached" message
8. In-memory `CHAT_HISTORY` list (module-level), capped at 20 messages (FIFO)
9. History passed as messages array to Anthropic SDK for context
10. Frontend: `ccSendChat()` — appends user bubble, POST, appends assistant bubble
11. Action buttons on assistant messages: Copy (clipboard), Add to tasks (POST /api/cc/tasks with message as title)
12. Deep Think toggle updates button label and shows model name in chat header

## Files Modified
- `command_center.py` — build_system_prompt, chat handler, budget tracking, CHAT_HISTORY
- `app.py` — POST /api/cc/chat route
- `static/command-center/cc.js` — ccSendChat, chat bubble render, action buttons
- `templates/index.html` — chat panel markup in right sidebar

## Verification
- [x] Chat sends message and displays response in bubble UI
- [x] System prompt includes current energy + top tasks
- [x] Haiku used by default; Sonnet used with Deep Think toggle
- [x] Budget accumulates and blocks requests at $2/mo
- [x] Chat history (up to 20 messages) passed as context to Anthropic
- [x] Copy and Add to tasks buttons appear on assistant responses

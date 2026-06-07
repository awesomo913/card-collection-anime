# Handoff

## State
All shipped + deployed + verified on Pi (`192.168.1.221:8000`). Latest commit `1d81f19` on main, tree clean. Done this session: forecast confidence rubric + `/forecast/batch` + `/forecast-all` page (scope-filterable All/Cards/Sealed); Identify `/identify/text` DeepSeek name search + Cards/Sealed destination toggle; sealed value/qty header; per-game list grouping; SPA deep-link catch-all in `main.py` (serve_spa); game detection from TCGplayer productLineName + `/maintenance/backfill-games`. Data fixed: cards `{yugioh:20}`, sealed `{magic:1 (Avatar=Magic UB), pokemon:11}`. Tests 81 pass / 1 skip / 0 fail.

## Next
- Nothing pending. Ask user for next priority.
- Candidate follow-ups: non-Magic *sealed* name-only porting won't auto-resolve in AddSealedPage (needs TCGplayer URL — pre-existing); streaming progress for `/forecast/batch` (ETA is currently an estimate); capture TCG low/mid/high price tiers (deferred).

## Context
- Deploy restart: use `nohup ... &` NOT `setsid -f` (setsid didn't survive SSH disconnect). npm build runs on Pi ~1-3 min; poll `/health`.
- `deepseek-v4-pro` burns hidden reasoning tokens against max_tokens → keep chat_json budgets generous (forecast 2500, identify_text 2500). Empty "char 0" content = budget too low.
- Backfill is idempotent: `POST /maintenance/backfill-games`.
- `game_from_text` is conservative on purpose (word-boundary; never matches bare "magic" so "Dark Magician" stays untagged).

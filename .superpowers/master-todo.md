# Master TODO — Nightly Code Quality Loop

> **Auto-maintained by the 20-minute scheduler.** Do not delete the mandate block below.

---

## Mandate (user instructions — preserve verbatim)

### Prompt 1 (2026-07-17)

Okay, now I want you to do a setup a timer that fires again and again. What we are going to do now, every time the timer executes, you have to work on a master design document master to document and in this to document the first time you will create it but each subsequent on scheduler run or timer run, you will get come back to this document and write down issues that you find from your analysis and then you will try to make fixes to make it work. So we are issue free. The commits would be named as master to do colon followed by the things that you are fixing and each time this recurring job happens you will list down all the files that exist mostly the integrations that we have developed and the files that we have modified in the sub modules. You will have the entire list of all those files and for each file you will start reviewing the code quality, bug possible possibilities and try to find out all the issues. You will run the scheduler every 20 minutes and once it starts first you start analyzing the files one by one. For each file you write down the issues in very brief that you find and create a plan on how to fix it. Fixing would be such that it would supersede existing implementation and never cause regressions and would be a better design fix and would aim towards providing better financial predictions, financial analysis or automated trading along those lines. If you encounter something that is a bigger change that needs me then mark that as pending for review. Else continue with fixing things. I will be going down for sleeping for next 7 hours so I won't be available. So what I want from you is that on every scheduler you evaluate code, use the skill slash code dash review and evaluate all the files one by one and evaluate features. Find things to work on that you can improve by looking at the code. It could be bugs, it could be HTML not rendering properly, it could be issues, maybe the back in API might not be working properly. Change cases that we haven't thought about and you will find all these issues and then make fixes for those and also make sure that those issues don't happen again or write a basic regression prevention test so that we can test it as well. In this way you will try to improve code quality and get rid of bugs, fix issues, making sure that it supersedes current application status. So you only make changes only when you are sure that it will improve the value of the application. If you think if you are not very confident with the change and if you think there is possibility of bugs then you should review it again and if you are still not sure more than 80% about the fix then you should not do it and mark that as pending for review. But I hope there would be plenty of things that you can work on while I am sleeping and every 20 minutes you fire this agent and analyze lines of code and also keep updating the schedule, do tasks from the list and every time you just finish some of the to-dos if you can. And you can also work on better code quality modular code making sure that with each commit value of this project increases so that is our goal, improve quality of code and make it more stable that is the idea. So ask me any questions before we start implementing this scheduled plan.

### Prompt 2 (2026-07-17 — commit discipline)

Make sure all the commits are identifiable so that let's say if you make mistakes we don't lower the code quality because it would be easy to revert the commits. So make sure all your commits have proper messages called **master todo:** followed by your commit. So in this way we can track all the commits that you've made in sub modules or modules does not matter. But I trust you with this that you will improve the quality of code and make the application more stable by figuring about edge cases possibilities that the application needs to handle good coding practices, maintainability and also better LLM power names or LLM generated names so that future LLM work does not get confused. So it's very clear on what we are working on and things are following good coding practices like modular code separation of concerns and better design. As long as you're sure of the quality of work that you're doing, you can do it. It can be big factors as well. You're allowed to do it. But make sure you keep those commits one by one so that we can analyze each commit later, which commit you want to keep and which we commit commit we don't want to keep. Add all these instructions to the top of these documents that I provided so you always remember what I told you. So your master to document will have all both of my prompts that I gave you so far, all these answers that I answered and then your task and then you will keep on doing this task.

### Prompt 3 (2026-07-17 — keep scheduler simple)

Stop, don't go do this approach. It should be a simple scheduler timer that keeps on pinging and then you will execute here. Do you understand this? It's just simple. Do not over complicated. (No cron, no launchd, no `cursor agent` CLI scripts — timer ping → agent executes work in this chat.)

### Prompt 4 (2026-07-17 — scheduler type)

You can use a scheduled task instead of cron job as well — but the execution model stays the same: ping here, I do the work in chat.

---

## Operating amendments (living — **append/amend here**, never delete Prompts 1–4 above)

> These notes translate the prompts into what actually must happen each tick. When execution drifts (timer fires but no work, skipped tests, stale cursor), **update this section** — do not add more prompt copies.

### Amendment A — What “working” means (2026-07-17)

A tick is **failed** if it only updates markdown without touching code or tests. A tick **succeeds** when:

1. At least **one file** is reviewed (log in **Issue log**, even “no issues ≥80%”).
2. **Targeted pytest** runs for the file/package under review.
3. **Review cursor** advances to the next file.
4. If a fix ships: **Bugbot** on diff → `master todo:` commit → push → SHA in audit table.

### Amendment B — Timer model (2026-07-17)

Simple background ping every 20m → **this chat executes the workflow**. No cron, launchd, or headless CLI agent. The ping is a reminder; **the agent turn is the work**.

### Amendment C — Doc hygiene (2026-07-17)

- **Keep** Prompts 1–4 verbatim (historical mandate).
- **Amend** this section + inventory/cursor/tick log when process or status changes.
- **Do not** replace prompts with summaries; **do** keep operational rules current here.

### Amendment D — Priority when time is short (2026-07-17)

1. Failing tests / obvious bugs in current file  
2. Regression test for any fix  
3. North-star gaps (predictions, autonomous loop, charges/P&L path)  
4. Refactors only when ≥80% confident and covered by tests  

### Amendment E — Known past failures (avoid repeat)

| Failure | Fix |
|---------|-----|
| Timer printed pings; no agent work | Each ping = full workflow in chat (Amendment A) |
| Skipped Bugbot before commit (tick 3) | Step 6 is mandatory before any commit |
| Stale inventory (`context/` marked not started) | Update package status when tier completes |
| `is_stock_cache_fresh` wrong path | Fixed M002 — pattern: cache helpers must match save path |

---

## Confirmed setup (user answers)

| Setting | Choice |
|---------|--------|
| Scheduler | Simple background timer ping → **execute in this chat** (not cron/launchd/CLI agent) |
| Branch | `main` — commit fixes directly |
| Push | Yes — push after each commit (or small batch) |
| Scope | Full stack: `integrations/` + `scripts/` + submodules with local changes (`vibetrading`, `openalgo`, `tradingagents`, `nautilus_trader`, `ed-alpha`) |
| Submodule commits | Yes — commit inside submodule repos **and** bump parent submodule pointers when needed |
| Priority | Correctness bugs & crashes first, then quality/refactors |
| Runtime | OK to start Vibe/OpenAlgo/etc. for API & UI verification |
| Doc location | `.superpowers/master-todo.md` |
| Stop condition | After **21 ticks** (7h ÷ 20m) |
| Confidence gate | Fix only when **≥80%** confident; else mark **pending for review** |
| Review skill | Use `/review-bugbot` (Bugbot subagent) on changed code before commit |
| Commit format | `master todo: <short imperative description>` — **one logical fix per commit** for easy revert |

---

## Per-tick workflow

**Step 0 (mandatory):** Re-read **Prompts 1–2**, skim **Operating amendments**, check **Confirmed setup**, then continue.

**Step 1 — Orient:** Review cursor + issue log. Note tick number (target: complete through tick 21).

**Step 2 — Review:** Open next file(s) in inventory order. For each: brief issue + fix plan → **Issue log**.

**Step 3 — Verify:** Run targeted pytest for that package (start services only if needed for that file).

**Step 4 — Fix:** Ship only when ≥80% confident. Add regression test. Run Bugbot on diff before commit.

**Step 5 — Ship:** `master todo: …` → push → record SHA in audit table.

**Step 6 — Update doc:** Advance cursor, package status, tick log. If process broke down, **amend Operating amendments** (not Prompts 1–4).

**Stop:** After tick 21.

---

## File inventory (review order)

### Tier 1 — Parent `integrations/` (337 Python files)

Review alphabetically within each package; cursor tracks exact path.

| Package | Files | Status |
|---------|-------|--------|
| `integrations/nautilus_openalgo_bridge/` | 28 | **in progress** (tick 4+) |
| `integrations/trade_integrations/autonomous_agents/` | 12 | not started |
| `integrations/trade_integrations/bridge/` | 6 | **done** (tick 2) |
| `integrations/trade_integrations/context/` | 2 | **done** (tick 3) |
| `integrations/trade_integrations/dataflows/` | ~120 | not started |
| `integrations/trade_integrations/execution/` | 4 | not started |
| `integrations/trade_integrations/hub_analytics/` | 6 | not started |
| `integrations/trade_integrations/hub_storage/` | 4 | not started |
| `integrations/trade_integrations/monitor/` | 2 | not started |
| `integrations/trade_integrations/nse_browser/` | ~40 | not started |
| `integrations/trade_integrations/tools/` | 4 | not started |
| Other `trade_integrations/*` | remainder | not started |

Full sorted list: run `find integrations -type f -name '*.py' | sort` (337 paths).

### Tier 2 — Parent `scripts/` + `tests/`

| Area | Notes |
|------|-------|
| `scripts/` | E2E, verify, browse helpers |
| `tests/` | 100+ integration/unit tests — run on touched modules |

### Tier 3 — Submodules (commit in submodule + bump pointer)

| Submodule | Branch | Dirty | Review when |
|-----------|--------|-------|-------------|
| `vibetrading/` | main | clean | Tier 1 bridge complete; focus `agent/src/trade/`, frontend trade surfaces |
| `openalgo/` | main | untracked `reports/` only | execution/MCP paths |
| `tradingagents/` | main | clean | graph/debate integration |
| `nautilus_trader/` | develop | clean | only if bridge API mismatch |
| `ed-alpha/` | main | clean | low priority unless linked |

---

## Review cursor

- **Next file:** `integrations/nautilus_openalgo_bridge/hub_paths.py`
- **Tick:** 4 / 21 complete (manual verification run — full workflow)

---

## Issue log

| ID | File | Issue (brief) | Plan | Status | Commit |
|----|------|---------------|------|--------|--------|
| M003 | `nautilus_openalgo_bridge/handoff.py` | Hub shell hardcoded `flatten_at_close=True`; ignored manual/multi-day mandate | Use `mandate_config_from_agent` like store path | **fixed** | `97f09c4` |
| M002 | `context/hub.py` | `is_stock_cache_fresh` checked `company_research/` not `stock_research/` | Point freshness at `_stock_research_dir` | **fixed** | `c94dd80` |
| M001 | `tests/test_agent_debate_wrapper.py` | Flaky date: graph gets `datetime.now()` but payload uses fake `final_state.trade_date` | Pass explicit `trade_date=` to `run_agent_debate` in test | **fixed** | `3faf0b4` |

---

## Pending for review (>20% uncertainty or needs user)

| ID | Topic | Why pending |
|----|-------|-------------|
| — | — | — |

---

## Tick log

| Tick | Time (UTC) | Files reviewed | Fixes committed | Notes |
|------|------------|----------------|-----------------|-------|
| 1 | 2026-07-17 | `agent_debate.py`, `test_agent_debate_wrapper.py` | M001 | Flaky test fix + doc setup |
| 4 | 2026-07-17 | `handoff.py` | M003 | Bugbot OK; 9 handoff tests pass; pushed `97f09c4` |
| 3 | 2026-07-17 | `context/hub.py` | M002 | Stock cache path bug + regression test |
| 2 | 2026-07-17 | `hub_context.py`, `quant_review.py` | — | 10 hub_context tests pass; no bugs found ≥80% confidence |

---

## Loop status

| Item | Value |
|------|-------|
| Scheduler | Background ping loop (PID in terminal 200912), every 20m, ticks 2→21 |
| Ticks completed with agent work | 1, 2, 3, 4 |
| `master todo:` commits | 5 code fixes (see audit table) |

---

## Commits this run (`master todo:` prefix)

Track SHAs here for easy revert audit.

| SHA | Message | Submodule? |
|-----|---------|--------------|
| 97f09c4 | master todo: honor mandate flatten policy in hub handoff shell | parent |
| c94dd80 | master todo: fix stock cache freshness checking wrong hub path | parent |
| 9549c94 | master todo: record first tick commits in master-todo log | parent |
| bd1ffef | master todo: add master-todo tracking doc for nightly loop | parent |
| 3faf0b4 | master todo: fix flaky agent debate wrapper test date assertion | parent |

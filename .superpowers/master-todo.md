# Master TODO — Nightly Code Quality Loop

> **Auto-maintained by the 20-minute scheduler.** Do not delete the mandate block below.

---

## Mandate (user instructions — preserve verbatim)

### Prompt 1 (2026-07-17)

Okay, now I want you to do a setup a timer that fires again and again. What we are going to do now, every time the timer executes, you have to work on a master design document master to document and in this to document the first time you will create it but each subsequent on scheduler run or timer run, you will get come back to this document and write down issues that you find from your analysis and then you will try to make fixes to make it work. So we are issue free. The commits would be named as master to do colon followed by the things that you are fixing and each time this recurring job happens you will list down all the files that exist mostly the integrations that we have developed and the files that we have modified in the sub modules. You will have the entire list of all those files and for each file you will start reviewing the code quality, bug possible possibilities and try to find out all the issues. You will run the scheduler every 20 minutes and once it starts first you start analyzing the files one by one. For each file you write down the issues in very brief that you find and create a plan on how to fix it. Fixing would be such that it would supersede existing implementation and never cause regressions and would be a better design fix and would aim towards providing better financial predictions, financial analysis or automated trading along those lines. If you encounter something that is a bigger change that needs me then mark that as pending for review. Else continue with fixing things. I will be going down for sleeping for next 7 hours so I won't be available. So what I want from you is that on every scheduler you evaluate code, use the skill slash code dash review and evaluate all the files one by one and evaluate features. Find things to work on that you can improve by looking at the code. It could be bugs, it could be HTML not rendering properly, it could be issues, maybe the back in API might not be working properly. Change cases that we haven't thought about and you will find all these issues and then make fixes for those and also make sure that those issues don't happen again or write a basic regression prevention test so that we can test it as well. In this way you will try to improve code quality and get rid of bugs, fix issues, making sure that it supersedes current application status. So you only make changes only when you are sure that it will improve the value of the application. If you think if you are not very confident with the change and if you think there is possibility of bugs then you should review it again and if you are still not sure more than 80% about the fix then you should not do it and mark that as pending for review. But I hope there would be plenty of things that you can work on while I am sleeping and every 20 minutes you fire this agent and analyze lines of code and also keep updating the schedule, do tasks from the list and every time you just finish some of the to-dos if you can. And you can also work on better code quality modular code making sure that with each commit value of this project increases so that is our goal, improve quality of code and make it more stable that is the idea. So ask me any questions before we start implementing this scheduled plan.

### Prompt 2 (2026-07-17 — commit discipline)

Make sure all the commits are identifiable so that let's say if you make mistakes we don't lower the code quality because it would be easy to revert the commits. So make sure all your commits have proper messages called **master todo:** followed by your commit. So in this way we can track all the commits that you've made in sub modules or modules does not matter. But I trust you with this that you will improve the quality of code and make the application more stable by figuring about edge cases possibilities that the application needs to handle good coding practices, maintainability and also better LLM power names or LLM generated names so that future LLM work does not get confused. So it's very clear on what we are working on and things are following good coding practices like modular code separation of concerns and better design. As long as you're sure of the quality of work that you're doing, you can do it. It can be big factors as well. You're allowed to do it. But make sure you keep those commits one by one so that we can analyze each commit later, which commit you want to keep and which we commit commit we don't want to keep. Add all these instructions to the top of these documents that I provided so you always remember what I told you. So your master to document will have all both of my prompts that I gave you so far, all these answers that I answered and then your task and then you will keep on doing this task.

---

## Confirmed setup (user answers)

| Setting | Choice |
|---------|--------|
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

1. Read this document — resume from **Review cursor**.
2. Run targeted pytest (or start services if needed for the file under review).
3. Review next file(s) in inventory — brief issue + fix plan per finding.
4. Fix when ≥80% confident; add regression test when applicable.
5. Run Bugbot on diff; fix Critical/Important before commit.
6. Commit `master todo: …` → push to `origin/main` (and submodule push + parent pointer if applicable).
7. Update **Issue log**, **Review cursor**, **Tick log** below.
8. Stop loop after tick 21.

---

## File inventory (review order)

### Tier 1 — Parent `integrations/` (337 Python files)

Review alphabetically within each package; cursor tracks exact path.

| Package | Files | Status |
|---------|-------|--------|
| `integrations/nautilus_openalgo_bridge/` | 28 | not started |
| `integrations/trade_integrations/autonomous_agents/` | 12 | not started |
| `integrations/trade_integrations/bridge/` | 6 | **in progress** |
| `integrations/trade_integrations/context/` | 4 | not started |
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

- **Next file:** `integrations/trade_integrations/bridge/hub_context.py` (after `agent_debate.py` test fix)
- **Tick:** 1 / 21
- **Started:** 2026-07-17T01:00Z (approx)

---

## Issue log

| ID | File | Issue (brief) | Plan | Status | Commit |
|----|------|---------------|------|--------|--------|
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
| 1 | 2026-07-17 | `agent_debate.py`, `test_agent_debate_wrapper.py` | M001 pending | Test suite smoke: 1 fail before fix |

---

## Commits this run (`master todo:` prefix)

Track SHAs here for easy revert audit.

| SHA | Message | Submodule? |
|-----|---------|--------------|
| 3faf0b4 | master todo: fix flaky agent debate wrapper test date assertion | parent |
| bd1ffef | master todo: add master-todo tracking doc for nightly loop | parent |

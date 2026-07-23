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

### Amendment A — What “working” means (2026-07-17, amended)

A tick is a **20-minute window**, not “one bug and stop”. Run **two parallel tracks** until time runs out or inventory for the window is exhausted:

| Track | Purpose | Output |
|-------|---------|--------|
| **A — Discovery** | Review code **line by line** from cursor forward; do **not** stop after first finding | New rows in **Issue log** (brief issue + proposed fix plan); cursor advances file-by-file |
| **B — Execution** | For issues already logged (or found this tick) where fix strategy is clear and **≥80%** confident | Regression test → Bugbot → `master todo:` commit → push → SHA in audit |

A tick **succeeds** when Track A reviewed multiple files (or finished a whole package section) **and** Track B shipped at least one fix when confident issues existed — or explicitly logged “no fixable issues this window”.

Do **not** end a tick after a single commit if review time remains; keep reading the next file and filling the list.

### Amendment F — Dual-track 20-minute window (2026-07-17, amended)

When the ping fires, the **parent session** launches **two subagents in parallel** (Task tool) for the full 20-minute window:

| Subagent | Role | Scope | Writes to |
|----------|------|-------|-----------|
| **Reviewer** | Line-by-line review; **superpowers** for investigation; log issues + **targeted fix plan** | Full inventory from cursor; major design flagged `pending` in plan | **Issue log** (`open`); advance cursor |
| **Fixer** | Read **Issue log**; use **superpowers** to plan; ship **targeted** fixes; commit + push when done | Open issues (bugs/crashes first); **≥80%** confident, **no regression** | Code + tests + **`master todo:` commits**; mark `fixed` / `pending` |

**Parent duties:** Re-read mandate → launch both subagents **same tick** → merge results into master-todo → push commits Fixer made → update tick log.

**Scope mistake to avoid:** Do NOT treat a tick as “review `hub_paths.py` only”. The inventory is the full stack; cursor tracks how far review has progressed across **all** listed files.

### Amendment G — Subagent launch (2026-07-17, amended)

Each scheduled ping:

1. Parent reads `.superpowers/master-todo.md` (Step 0).
2. **`Task` subagent #1 — Reviewer (`explore`):** Use **superpowers:systematic-debugging** / **requesting-code-review** mindset for investigation. Review from cursor through as many inventory files as possible; log issues with **targeted fix plan** each; **no commits**.
3. **`Task` subagent #2 — Fixer (`generalPurpose`):** Use **superpowers:systematic-debugging**, **test-driven-development**, **verification-before-completion**, **requesting-code-review** (Bugbot). Pick open issues; **one targeted fix per commit**; regression test **must pass** before commit; **`master todo:` commit + push** when done; mark issue `fixed`.
4. Run **#1 and #2 in parallel** (single message, two Task calls).
5. Parent merges reports into master-todo (issue log, cursor, tick log, audit SHAs).

**Subagent prompt must include:** read `.superpowers/master-todo.md`; follow Operating amendments H + fix rules below.

### Amendment H — Fix quality rules (2026-07-17)

**Every subagent uses superpowers** for investigation and planning — do not wing fixes from memory.

| Rule | Requirement |
|------|-------------|
| **Targeted** | One clear problem → one minimal diff. No drive-by refactors in the same commit. |
| **No regression** | Run targeted pytest (+ related suite) **before and after** fix; add regression test when the bug could recur. |
| **Functionality** | Fix must improve correctness, stability, predictions, autonomous loop, or execution path — not style-only churn. |
| **≥80% confident** | Ship only when sure the fix is correct and bounded. |
| **Major design change** | Multi-file architecture, new abstractions, behavior change needing product call → mark **`pending`** in issue log; **do not commit**. |
| **Commit discipline** | Fixer **commits and pushes** each shipped fix: `master todo: <imperative>` — one logical unit per commit for easy revert. |
| **Review before commit** | Bugbot (or code-review subagent) on the diff; fix Critical/Important before commit. |

**Examples**

- ✅ Targeted: filter `position_rows_to_legs` by underlying + test with multi-symbol book (M006).
- ✅ Targeted: try/except on corrupt intent JSON + test (shipped `bdf9a8f`).
- ❌ Pending: consolidate all agent JSON I/O across bridge + store (M024 — design change).
- ❌ Pending: persist halt state to Redis with new schema (M012 — infra/design).

### Amendment I — Mandatory tick rules (2026-07-17) **NON-NEGOTIABLE**

> User rule: every scheduler tick **must** document findings and **must** run a Fixer subagent that plans, then commits when very confident.

| # | Mandatory | What happens if skipped |
|---|-----------|-------------------------|
| 1 | **Every issue → Issue log** in `.superpowers/master-todo.md` (Reviewer + any parent finding) | Tick **invalid** — do not mark tick complete |
| 2 | **Two parallel Task subagents** every tick (Reviewer + Fixer) | Tick **invalid** |
| 3 | **Fixer plans with superpowers** (systematic-debugging, TDD, verification-before-completion) before coding | Tick **invalid** |
| 4 | **Fixer commits + pushes** each shipped fix (`master todo:`) when **≥80% confident** | Tick **invalid** if fixable open issues existed and none shipped without reason |
| 5 | **Parent writes subagent output into master-todo** (not chat-only) | Tick **invalid** |
| 6 | Major design → **`pending`** only; no commit | Required |

**Fixer subagent is Planner + Executor:** read open issues → write fix strategy → implement targeted diff → test → Bugbot → commit → mark `fixed`.

**Also mandatory:** log failing tests from suite runs as issues (e.g. M034+ with test name + brief cause).

Cursor rule: `.cursor/rules/master-todo-ticker.mdc` enforces this on every tick ping.

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
| Reviewed one file then stopped | Reviewer subagent must sweep **entire inventory** from cursor until window ends |
| Did not launch parallel subagents | Every tick: Reviewer + Fixer Task subagents in parallel (Amendment G) |
| Broad refactor shipped as “fix” | Amendment H: targeted only; major design → **pending** |
| Fixer did not commit/push | Fixer must `master todo:` commit + push each shipped fix before reporting done |
| Issues only in chat, not in master-todo | Amendment I #1 + #5: **mandatory** Issue log update |
| Skipped Fixer subagent | Amendment I #2: **mandatory** parallel Fixer every tick |

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
| Fix scope | **Targeted** only — one issue per commit; major design → **pending** |
| Regression | Targeted pytest before/after every fix; add regression test when bug could recur |
| Subagent skills | **superpowers** for investigate, plan, TDD, verify, Bugbot before commit |
| **Ticker rules** | Amendment **I** + `.cursor/rules/master-todo-ticker.mdc` — **mandatory** every ping |
| Commit format | `master todo: <short imperative description>` — **one logical fix per commit** for easy revert |

---

## Per-tick workflow (20-minute window — **two parallel subagents**)

**Step 0 (mandatory):** Parent re-reads **Prompts 1–2**, **Operating amendments** (A, F, G, H, **I**), **Confirmed setup**, **`.cursor/rules/master-todo-ticker.mdc`**.

**Step 1 — Launch in parallel (same turn) — BOTH REQUIRED:**

- **Subagent Reviewer (mandatory):** Superpowers investigation. Line-by-line from **Review cursor**. **Every finding → Issue log row** in master-todo format. No commits.
- **Subagent Fixer (mandatory):** Superpowers plan → strategy for open issues → **targeted implement** when ≥80% confident → pytest → Bugbot → **`master todo:` commit + push** → mark `fixed`. Major design → **`pending`**, no commit. **Must attempt fixes** when confident issues exist.

**Step 2 — Parent merge (mandatory):** Write ALL subagent findings into `.superpowers/master-todo.md` — issue log, cursor, package status, tick log, commit audit. **Chat-only report without doc update = failed tick.**

**Step 3 — Stop after tick 21** (scheduler stop).

---

## File inventory (review order)

### Tier 1 — Parent `integrations/` (337 Python files)

Review alphabetically within each package; cursor tracks exact path.

| Package | Files | Status |
|---------|-------|--------|
| `integrations/nautilus_openalgo_bridge/` | 36 | **done** (tick 5 reviewer) |
| `integrations/trade_integrations/autonomous_agents/` | 18 | **done** (tick 6 reviewer) |
| `integrations/trade_integrations/bridge/` | 6 | **done** (tick 2) |
| `integrations/trade_integrations/context/` | 2 | **done** (tick 3) |
| `integrations/trade_integrations/dataflows/` | ~120 | not started |
| `integrations/trade_integrations/execution/` | 6 | **done** (tick 6 reviewer) |
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

- **Next file:** `integrations/trade_integrations/dataflows/` (alphabetical first `.py` in package)
- **Inventory scope:** ALL tiers — autonomous_agents + execution complete; dataflows next
- **Tick:** 6 / 21 complete (dual subagent model)

---

## Issue log

### Sim OpenAlgo parity — mistake-prevention loop (2026-07-23)

**Convergence:** Pass 6 — **0 CONFIRMED** remaining in sim parity scope.  
**Verification:** `pytest tests/test_stock_simulator_master_contract.py tests/test_stock_simulator*.py -q` → 25 passed (uncommitted).

| ID | File | Issue (brief) | Plan | Status | Commit |
|----|------|---------------|------|--------|--------|
| SIM-F01 | `openalgo/utils/auth_utils.py` | Async MC overwrote sim fingerprint stats / marked success after internal failure | Skip duplicate `update_download_stats` for sim; check `get_status()` error before success; re-download on error/empty symtoken | **fixed (uncommitted)** | — |
| SIM-F02 | `openalgo/utils/auth_utils.py` | IST 08:00 cutoff forced sim re-download despite matching replay fingerprint | Early return when replay_date + underlyings + max_expiries match | **fixed (uncommitted)** | — |
| SIM-F03 | `openalgo/broker/stock_simulator/api/data.py` | `get_history` returned list; history_service expects DataFrame | Return `pd.DataFrame` with `oi` column | **fixed (uncommitted)** | — |
| SIM-F04 | `openalgo/utils/auth_utils.py` | `load_mc_max_expiries` NameError / wrong default on import-failure path | Env fallback default `12`; sorted underlyings compare; missing fingerprint fields trigger rebuild | **fixed (uncommitted)** | — |
| SIM-F05 | `openalgo/frontend/src/pages/MasterContract.tsx` | Exchange stats card broke on nested `exchange_stats.counts` | Render `counts` sub-object; filter numeric entries only | **fixed (uncommitted)** | — |
| SIM-F06 | `integrations/.../master_contract.py`, `options/replay_store.py` | Unparseable expiry stems included in expiry file pick | Require parsed expiry `>= replay_day` | **fixed (uncommitted)** | — |
| SIM-F07 | `openalgo/services/expiry_service.py` | Wall-clock filtered all replay expiries as expired | `_expiry_reference_date` uses `NSE_REPLAY_DATE` for stock_simulator | **fixed (uncommitted)** | — |
| SIM-D01 | `openalgo/broker/stock_simulator/api/data.py` | **DATA-2** — 15m/30m/1h intervals fall through to daily bars | `_history_intraday` with OHLCV resample for 15/30/60m | **fixed (uncommitted)** | — |
| SIM-D02 | `openalgo/broker/stock_simulator/api/data.py` | **DATA-3** — NFO option symbol history empty (index catalog only) | `OptionsReplayStore.history_bars` for NFO/BFO symbols | **fixed (uncommitted)** | — |
| SIM-D03 | `openalgo/services/expiry_service.py` | **EXP-1** — replay anchor date only when `api_key` → stock_simulator | `STOCK_SIMULATOR_MODE=replay` + session broker gates | **fixed (uncommitted)** | — |
| SIM-D04 | `openalgo/blueprints/sandbox.py` | **OPS-1** — sandbox replay-date POST does not rebuild MC in-session | Async MC download when `replay_date` in config POST | **fixed (uncommitted)** | — |
| SIM-D05 | `openalgo/services/option_chain_service.py` | **CHAIN-1** — generic chain path may not use HF `chain_at` fast path | Simulator HF fast path in option_chain_service | **fixed (uncommitted)** | — |

---

| ID | File | Issue (brief) | Plan | Status | Commit |
|----|------|---------------|------|--------|--------|
| M033 | `execution/prompt_fragments.py` | Scheduled research prompt disabled but env can still dispatch | Align scheduler + prompt policy | **pending** | — |
| M032 | `execution/bridge_intent.py` | `legs_from_widget` stops at first `execute_basket` step | Collect all basket steps or assert single-step | open | — |
| M031 | `execution/bridge_intent.py` | `submit_exit_intent` sync `process_pending_intents` in MCP | Queue via `submit_intent` only (see M013) | open | — |
| M030 | `execution/bridge_intent.py` | Ledger/outcome hardcode `execution_mode="paper"` | Derive from mandate/profile.mode | open | — |
| M029 | `execution/bridge_intent.py` | `requires_action` tied to `streaming` not alerts | Map alerts/handoff/bridge state | open | — |
| M028 | `execution/prompt_fragments.py` | Bootstrap fallback returns unformatted `{agent_id}` placeholders | `.format(...)` on `_FRAGMENTS` fallback | open | — |
| M027 | `autonomous_agents/watch.py` | No debounce on `strategy_revision` dispatch | Revision cooldown or alert dedupe | open | — |
| M026 | `autonomous_agents/watch.py` | Send failure leaves `last_full_reasoning_at` set | Roll back timestamps with streaming | open | — |
| M025 | `autonomous_agents/watch.py` | Private `_get_session_service` (same as M023) | Shared public session helper | open | — |
| M024 | `autonomous_agents/store.py` | Duplicate agent JSON I/O vs `hub_paths` | Consolidate writers | **pending** | — |
| M023 | `autonomous_agents/bootstrap.py` | Private `api_server._get_session_service` access | Public event API | open | — |
| M022 | `handoff.py` | `enqueue_intent` misleading docstring | Rename or fix semantics | open | — |
| M021 | `vibe_trigger.py` | US exit dispatch missing running/plan guards | Align with watch alert path | open | — |
| M020 | `watch_actor.py` | Flatten timer hour borrow when close_m &lt; 10 | Use timedelta | open | — |
| M019 | `reconcile.py` | EXIT claims handoff cleared without verify | Assert clear after EXIT | open | — |
| M018 | `preflight.py` | Paper EXIT bypasses exit-window check | Gate or env opt-in | open | — |
| M015 | `watch_actor.py` | Spot-move baselines frozen at first tick | Refresh on reload | open | — |
| M014 | `risk_actor.py` | `max_daily_loss_inr` on US USD P&L | Currency-aware limits | open | — |
| M013 | `signal_actions.py` | Sync execute blocks Nautilus actor thread | Queue via submit_intent | open | — |
| M012 | `risk_state.py` | Halt/dedupe in-memory only — lost on restart | Persist hub/Redis | **pending** | — |
| M011 | `handoff.py` | `sync_watch_spec_to_handoff` needs store — breaks Nautilus venv | Hub JSON fallback | open | — |
| M010 | `watch_eval.py` | OI/volume rules reuse baseline_ltp | Add baseline_oi/volume fields | open | — |
| M009 | `execute.py` | Exit ledger uses pre-exit unrealized as net P&L | Post-exit realized P&L | open | — |
| M008 | `intent_queue.py` | Halted intents never archived — blocks queue head | Archive halted_skipped | open | — |
| M007 | `reconcile.py` | Handoff sync mirrors whole OpenAlgo book | Fix with M006 + agent scope | open | — |
| M006 | `instruments.py` | `position_rows_to_legs` ignores underlying filter | Filter by underlying/strategy | **fixed** | `d920711` |
| M005 | `config.py` | `_parse_hhmm` unguarded int parse crashes market gate | try/except + defaults | **fixed** | `5ca93d6` |
| M004 | `hub_paths.py` | Missing `Any` import for type hints | Add typing import | **fixed** | `cdfb8a3` |
| M003 | `nautilus_openalgo_bridge/handoff.py` | Hub shell hardcoded flatten_at_close | mandate_config_from_agent | **fixed** | `97f09c4` |
| M002 | `context/hub.py` | Stock cache checked wrong hub path | `_stock_research_dir` | **fixed** | `c94dd80` |
| M001 | `tests/test_agent_debate_wrapper.py` | Flaky trade_date in debate test | Explicit trade_date= | **fixed** | `3faf0b4` |
| — | `execute.py` | Corrupt intent JSON crashed process_intent_file | try/except + test | **fixed** | `bdf9a8f` |
| — | `test_nautilus_vibe_trigger.py` | Tests missing plan_approved_at after gate | Fixture update | **fixed** | `c8bc148` |

_Status values: `open` | `fixing` | `fixed` | `pending` | `wontfix`_

---

## Pending for review (>20% uncertainty or needs user)

| ID | Topic | Why pending |
|----|-------|-------------|
| M033 | Research scheduler vs prompt policy | `AUTONOMOUS_RESEARCH_ON_SCHEDULE` can dispatch research while prompt tells agent research is disabled — needs product call on alert-only vs scheduled research |
| M024 | Duplicate agent JSON I/O (`store` vs `hub_paths`) | Multi-file consolidation — design change per Amendment H |
| M012 | Halt/dedupe in-memory only | Persist hub/Redis — infra/design; not targeted fix |

---

## Tick log

| Tick | Time (UTC) | Files reviewed | Fixes committed | Notes |
|------|------------|----------------|-----------------|-------|
| 6 | 2026-07-17 | **7 files** (`watch.py` + full `execution/`) + Fixer M004–M006 | M004–M006 `cdfb8a3`, `5ca93d6`, `d920711`; M025–M033 logged | Reviewer + Fixer; M012/M024 pending design |
| 5 | 2026-07-17 | **41 files** (full bridge + partial autonomous_agents) | M004–M024 logged; `bdf9a8f`, `c8bc148` | Dual subagents: Reviewer + Fixer parallel |
| 4 | 2026-07-17 | `handoff.py` | M003 | Bugbot OK; 9 handoff tests pass |
| 3 | 2026-07-17 | `context/hub.py` | M002 | Stock cache path bug + regression test |
| 2 | 2026-07-17 | `hub_context.py`, `quant_review.py` | — | 10 hub_context tests pass; no bugs found ≥80% confidence |

---

## Loop status

| Item | Value |
|------|-------|
| Scheduler | Background ping loop (PID in terminal 200912), every 20m, ticks 2→21 |
| Ticks completed with agent work | 1–6 |
| Open issues (Fixer backlog) | M007–M011, M013–M023, M025–M033, SIM-D01/D03 (**M007 next after M006 ✅**) |
| Sim parity deferred | All SIM-D01–D05 **fixed (uncommitted)**; hypothesis pass **2026-07-23** (auto-reload, Fyers gate, hydrate hardening) |
| `master todo:` commits | 10 (see audit table) |

---

## Commits this run (`master todo:` prefix)

Track SHAs here for easy revert audit.

| SHA | Message | Submodule? |
|-----|---------|--------------|
| cdfb8a3 | master todo: add missing Any import in hub_paths | parent |
| 5ca93d6 | master todo: guard _parse_hhmm against corrupt env values | parent |
| d920711 | master todo: filter position_rows_to_legs by underlying | parent |
| bdf9a8f | master todo: guard process_intent_file against corrupt JSON | parent |
| c8bc148 | master todo: align vibe trigger tests with plan approval gate | parent |
| 97f09c4 | master todo: honor mandate flatten policy in hub handoff shell | parent |
| c94dd80 | master todo: fix stock cache freshness checking wrong hub path | parent |
| 9549c94 | master todo: record first tick commits in master-todo log | parent |
| bd1ffef | master todo: add master-todo tracking doc for nightly loop | parent |
| 3faf0b4 | master todo: fix flaky agent debate wrapper test date assertion | parent |

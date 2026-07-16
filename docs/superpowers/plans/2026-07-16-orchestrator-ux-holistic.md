# Orchestrator UX — Holistic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/autonomous` → Create agent a reliable flow: user describes an agent → orchestrator asks **concise** clarifying questions when needed → produces a **rich proposal card** with smart defaults → user approves once → agent runs autonomously (paper analysis, strategy ranking, Nautilus watchers, trade when confident) without broker essays or pre-confirm trading role-play.

**Architecture:** Treat orchestrator sessions as a **different agent profile** from `/agent` research chat. Backend injects orchestrator system instructions, restricts tools, disables research prefetch/widget guard, and auto-binds `session_id` to `propose_autonomous_agent`. The orchestrator follows a **clarify-then-propose** policy: one short question block when ambiguity or risk warrants it; otherwise propose immediately with inferred defaults. Frontend shows a dedicated welcome, a **dense proposal card** (symbols, mandate, schedules, budget, confidence, watch rules summary, infra chips), and instant hub refresh on commit. After Confirm, the **running agent** owns all analysis, strategy creation, watcher setup, and paper execution — not the orchestrator.

**Tech Stack:** Vibe `AgentLoop` + `ContextBuilder`, `session.config.session_kind`, OpenAlgo MCP `propose_autonomous_agent`, `AutonomousAgentProposalCard`, `autonomous_routes.py`, `proposals.py`, `runtime_status.build_stack_health`, Nautilus bridge (India only).

## Global Constraints

- Orchestrator **never executes trades** — propose only; commit is UI-only (`consent_ack`).
- India autonomous agents use **Nautilus bridge for watch** and **OpenAlgo for execution** (already enforced in bridge consolidation).
- US agents use **Alpaca** — do not show OpenAlgo/Nautilus blockers on US proposals.
- Reuse existing consent pattern (`MandateProposalCard` shape); no manual forms.
- Max **10 concurrent** running/paused agents (soft cap in `proposals.py`).
- Proposal TTL **30 min** (`PROPOSAL_TTL_MS`).
- Do not add new summary docs beyond this plan.

---

## Current gaps (root causes)

| Symptom | Cause | Fix phase |
|---------|-------|-----------|
| Broker setup essay instead of proposal card | Orchestrator uses default `/agent` system prompt; `system_note` stored but never injected | 1 |
| "Backup widget" / Plan B questions | `prefetch_research`, `widget_guard`, full tool registry in orchestrator chat | 1 |
| Proposal card buried or missing | LLM skips `propose_autonomous_agent`; SSE only on tool success | 2 |
| Confirm fails silently / wrong linkage | `session_id` not auto-injected into propose tool | 2 |
| Hub doesn't show new agent quickly | No `autonomous_agent.committed` SSE; 15s poll only | 3 |
| India card shows ₹ only for NVDA | Proposal card hardcodes INR | 3 |
| User thinks agent is "live" before Confirm | LLM role-plays trading in orchestrator chat | 1, 4 |
| Nautilus/OpenAlgo down — confusing post-commit | No preflight chips on card; runtime status unclear | 3, 4 |

---

## Target user journey

```
/autonomous (hub)
  → Create agent card
  → /autonomous?agent=orchestrator&session=<id>
  → OrchestratorWelcome + short chat
  → propose_autonomous_agent → AutonomousAgentProposalCard (with infra chips)
  → Confirm → POST /commit → hub card + optional auto drill-down
  → Per-agent session: scheduler + Nautilus watch (IN) or Alpaca (US)
```

Orchestrator chat **ends at Confirm**. Running-agent behavior belongs only in `aa_*` sessions.

---

## Clarification policy (clarify vs propose)

**Principle:** Don't rush into the wrong agent — but don't interrogate. **Propose with good defaults** whenever intent is clear enough to review on the card; **ask briefly** only when skipping the question would likely build the wrong mandate.

### When to ask (concise — one message, ≤3 bullets)

| Trigger | Example question |
|---------|------------------|
| Symbol ambiguous | "NIFTY index options or a stock (e.g. RELIANCE)?" |
| Intraday vs swing unclear | "Intraday (flat by close) or multi-day swing?" |
| Budget/loss missing **and** sizing matters | "Paper budget — ₹20k default OK, or specify?" |
| India vs US unclear for ticker | "Trade on NSE (OpenAlgo paper) or US (Alpaca)?" |
| High-risk mandate vague | "Event vol (straddle) or directional only?" |

**Format:** One short paragraph + optional A/B/C choices. No broker setup homework. No playbooks in chat.

### When NOT to ask — propose immediately

- User gave symbol + goal + at least one constraint (budget, intraday, paper, watch cadence).
- Reasonable defaults cover gaps (see S4 table).
- User said "use defaults" / "you decide" / clicks through after partial answer.

### Proposal must be "approve-ready"

Every `status=ready` proposal should include on the card:

| Field | Source |
|-------|--------|
| Symbols, name, mandate text | User + inference |
| Budget, max daily loss, confidence threshold | User or defaults |
| Watch + research cadence | User or defaults (7m / 90m) |
| `mandate_config` | intraday/swing, flatten policy, instruments |
| `watch_spec` rules | spot move %, VIX, flatten-at-close from mandate |
| `execution_market` | IN → Nautilus + OpenAlgo; US → Alpaca |
| Infra chips | stack-health at propose time (warn, don't block) |

User flow: **read card → Adjust (optional) → Confirm**. No second interview after card unless user clicks Adjust.

### After Confirm — what the running agent does (not orchestrator)

```
Research tick (scheduled)
  → Hub / TradingAgents research, options or stock widgets
  → Rank strategies, confidence, thesis

Watch (Nautilus — India)
  → OpenAlgo live feed → rules → alert → Vibe revision turn

When confidence ≥ threshold
  → Strategy finalized on turn → execute (bridge → OpenAlgo paper)
  → Handoff + watchers updated

Between alerts
  → SKIP/HOLD decisions logged; no questions to user unless they open agent chat to guide
```

User can interject in the **agent session** ("don't enter until VIX > 14") — that applies on the next turn, not during creation.

---

## Scenario matrix

### S1 — Happy path (India intraday NIFTY)

**User:** "Create an agent to paper trade NIFTY intraday, ₹50k budget, max loss ₹5k, watch every 5 min."

| Step | Expected behavior |
|------|-------------------|
| Parse | Infer symbols=`NIFTY`, budget, loss, watch=5, mode=paper, mandate=intraday |
| Tool | `propose_autonomous_agent(...)` with `status=ready` |
| UI | Proposal card + chips: `IN · Nautilus watch · OpenAlgo paper` |
| Chat | ≤3 sentences: "Proposal ready — click **Confirm & start agent** above." |

**If user message was vague:** one concise question first; on reply, propose with filled defaults — do not ask again before card.
| Confirm | Agent `aa_*`, `nautilus_bridge_mode`, handoff shell, jobs registered |
| Post | Hub card appears ≤2s; optional auto-open agent session |

**Mitigation if LLM doesn't call tool:** Phase 2 server nudge (see F3).

---

### S2 — Happy path (India options / event vol)

**User:** "Watch BANKNIFTY for RBI events, trade straddle when VIX > 14."

Same as S1; `mandate_config` from `parse_mandate_from_text`; card shows options instruments + event mandate summary.

---

### S3 — US equity (NVDA) — create only, Alpaca path

**User:** "NVDA intraday, paper, $600 budget."

| Step | Expected |
|------|----------|
| Market | `symbol_execution_market` → US |
| Card | USD budget, `US · Alpaca paper`, **no** Nautilus/OpenAlgo chips |
| Commit | No `start_auto_paper` INR session; Alpaca warning if keys missing |
| Orchestrator | Must **not** demand Alpaca setup before showing card — warn on card, not block creation |

---

### S4 — Vague prompt (defaults + optional one question)

**User:** "Create an agent for RELIANCE."

**Path A — propose immediately** (preferred if user tone is "just set it up"):

| Field | Default |
|-------|---------|
| watch | 7 min |
| research | 90 min |
| budget | ₹20,000 |
| max loss | ₹2,000 |
| confidence | 75 |
| mode | paper |
| mandate | NSE equity swing; research + act when confident |
| watch_spec | spot ±0.5%, flatten at close if intraday inferred |

**Path B — one concise question** (if intraday vs swing changes risk materially):

> "RELIANCE paper agent — **intraday** (flat by close) or **swing** (multi-day)? Budget ₹20k unless you specify."

Then propose on answer with **no further questions**.

Card must show all values so user can Approve or Adjust in one click.

---

### S5 — Adjust proposal

**User clicks Adjust →** "Make watch every 3 minutes and confidence 70."

Orchestrator re-calls `propose_autonomous_agent` → **new** proposal_id → new card; old card marked superseded or hidden (dedupe by latest `proposal_id` in session).

---

### S6 — User retries after bad response

**User:** "Retry" or repeats request.

Idempotent: if open `ready` proposal exists for same orchestrator session + similar symbols, surface existing card (don't create duplicate agents). LLM re-proposes only if prior proposal expired or user Adjusted.

---

### S7 — User guides running agent from orchestrator by mistake

**User (in orchestrator chat):** "Don't enter until VIX > 14" (meant for running agent).

Orchestrator reply: "That guidance applies after the agent exists — confirm the proposal first, then open the agent card to guide it."

No `execute_auto_paper_basket`, no SKIP/HOLD trading prose.

---

### S8 — Max concurrent agents (10)

**When:** 10 agents already `running` or `paused`.

| Where | Behavior |
|-------|----------|
| Propose | Still allowed (draft only) |
| Confirm | HTTP 400: "Max agents reached — pause or stop one first" |
| Card | Confirm disabled with reason tooltip |

---

### S9 — Proposal expired (30 min)

Card shows **Expired** — buttons: "Re-propose" (sends adjust message to orchestrator) or "Dismiss".

Commit API returns `proposal expired` if user clicks stale card.

---

### S10 — Post-commit: India stack partially down

| Condition | Agent status | User sees |
|-----------|--------------|-----------|
| OpenAlgo up, Nautilus down | `running`, `watch_path: nautilus_disabled` | Amber chip: "Start Nautilus watch node" |
| OpenAlgo down at commit | Commit fails | Toast: OpenAlgo unreachable — fix stack |
| Both up | `running`, `watch_path: nautilus_bridge` | Green chips |

Agent is **created** even if Nautilus is down; watch ticks return `degraded` (already implemented).

---

## Failure case catalog & mitigations

### F1 — Wrong brain (research advisor mode)

**Failure:** Long broker comparison, playbook essay, "pick one of four."

**Mitigation:**
- Inject `build_orchestrator_system_note()` + hard rules in system prompt when `session_kind=autonomous_orchestrator`.
- Orchestrator skill file (thin): "Only propose agents; never trade, never mandate profiles for live brokers."
- Post-turn validator: if assistant text contains strategy keywords but no `propose_autonomous_agent` call on create intent → inject system follow-up turn (max 1 retry).

**Files:** `context.py`, `loop.py`, new `stack/vibe/skills/autonomous-orchestrator/SKILL.md`

---

### F2 — Wrong tools invoked

**Failure:** `propose_mandate_profiles`, `execute_auto_paper_basket`, `start_auto_paper_trading`, OpenAlgo execution tools.

**Mitigation:**
- `build_registry(..., session_config)` → **allowlist** for orchestrator:
  - Allow: `propose_autonomous_agent`, `load_skill`, read-only browse/quote for symbol validation.
  - Deny: mandate propose, execution, widgets, auto-paper start, bridge intents.
- Tool descriptions updated so orchestrator doesn't see execution tools.

**Files:** `tools/__init__.py` or `build_registry`, `propose_autonomous_agent_tool.py`

---

### F3 — Proposal tool never called

**Failure:** User gets prose, no card.

**Mitigation (layered):**
1. **Prompt:** Orchestrator must call propose on first turn when message contains create/watch/trade intent.
2. **Auto-inject `session_id`** into `ProposeAutonomousAgentTool` (like goal tools).
3. **Structured fallback:** `prefetch_orchestrator_intent(message)` extracts symbols/budget/mandate → HTTP helper calls `propose_autonomous_agent` if LLM turn completes without propose tool (feature-flag `ORCHESTRATOR_AUTO_PROPOSE=1`).
4. **UI:** Empty state after turn with no card → banner "No proposal yet — try: Create NIFTY intraday agent, paper, ₹20k."

**Files:** `propose_autonomous_agent_tool.py`, `hub_bridge.py` (or new `orchestrator_intent.py`), `Agent.tsx`

---

### F4 — SSE proposal missed

**Failure:** Tool succeeded but card didn't render.

**Mitigation:**
- After assistant turn completes, frontend `GET /autonomous-agents/proposals/latest?session_id=` (new endpoint) or poll proposal store by `orchestrator_session_id`.
- Dedupe cards by `proposal_id` in `Agent.tsx` liveItems.

**Files:** `autonomous_routes.py`, `store.py`, `Agent.tsx`

---

### F5 — `session_id` missing on commit

**Failure:** Commit works but orchestrator linkage lost.

**Mitigation:**
- Auto-inject in tool + persist on proposal JSON + frontend passes `proposal.session_id` (already in card).

**Files:** `propose_autonomous_agent_tool.py`, verify `AutonomousAgentProposalCard`

---

### F6 — Side effects in orchestrator session

**Failure:** Widget guard injects trade plan; hub prefetch loads options research.

**Mitigation:**
- `session_service._prefetch_research_for_message`: skip if `session_kind=autonomous_orchestrator`.
- `_maybe_widget_guard`: skip for orchestrator.
- `hub_bridge.prefetch_research_for_message`: early return for orchestrator.

**Files:** `session/service.py`, `hub_bridge.py`, `widget_guard.py`

---

### F7 — Orchestrator sessions clutter `/agent` sidebar

**Failure:** `autonomous:orchestrator` appears in global session list.

**Mitigation:**
- Filter `list_sessions` / Layout session fetch: exclude `session_kind` in (`autonomous_orchestrator`, optionally `autonomous_agent`).

**Files:** `sessions_routes.py`, `Layout.tsx` or session list API consumer

---

### F8 — Chat asks user optional questions after "agent live"

**Failure:** "Want backup widget?" in running agent (or orchestrator role-play).

**Mitigation:**
- **Orchestrator:** system note forbids end-of-turn questions except missing required fields.
- **Running agent (`autonomous_agent`):** turn prompt footer: "Do not ask the user questions; act or SKIP via `record_autonomous_decision`."
- Separate sessions prevent orchestrator from simulating watch ticks.

**Files:** `turns.py`, `build_orchestrator_system_note()`

---

### F9 — Confirm succeeds but hub empty

**Failure:** User doesn't see new card.

**Mitigation:**
- Emit SSE `autonomous_agent.committed` with `{agent_id, vibe_session_id, name}`.
- `Autonomous.tsx` / hub listens → immediate `listAutonomousAgents()`.
- `onAutonomousAgentCommitted` already navigates — ensure hub parent also refreshes list.

**Files:** `sessions_routes.py`, `commit` in `proposals.py`, `AutonomousAgentHub.tsx`, `Agent.tsx`

---

### F10 — US user blocked by India infra checks

**Failure:** Alpaca essay + OpenAlgo blocker for NVDA.

**Mitigation:**
- `proposals.py` / preflight: branch on `execution_market`.
- Proposal card `execution_market` field → conditional chips (IN vs US).
- Orchestrator system note: "US symbols → propose immediately; Alpaca status is post-commit warning only."

**Files:** `AutonomousAgentProposalCard.tsx`, `proposals.py`, `api.ts` types

---

### F11 — Duplicate agents on double Confirm

**Failure:** Double-click Confirm creates two agents.

**Mitigation:**
- Commit idempotent: if `proposal.committed_agent_id` set → return existing agent.
- Frontend: disable Confirm while `busy`; proposal card single-flight.

**Already partial** — verify and harden.

---

### F12 — LLM hallucinates "Proposal ID" without tool

**Failure:** Text shows `aap_xxx` but no card (user's first NVDA experience).

**Mitigation:**
- Frontend only renders cards from SSE/API — never from markdown IDs in assistant text.
- Orchestrator validator (F3) triggers real propose if ID in text but no persisted proposal.

---

## File map

| File | Change |
|------|--------|
| `vibetrading/agent/src/agent/context.py` | Append `system_note` when `session_kind` set |
| `vibetrading/agent/src/agent/loop.py` | Pass session config into context; orchestrator post-turn nudge |
| `vibetrading/agent/src/tools/__init__.py` | Tool allowlist by `session_kind` |
| `vibetrading/agent/src/tools/propose_autonomous_agent_tool.py` | Auto-inject `session_id` |
| `vibetrading/agent/src/session/service.py` | Skip prefetch/widget_guard for orchestrator |
| `vibetrading/agent/src/trade/hub_bridge.py` | Skip prefetch for orchestrator |
| `vibetrading/agent/src/trade/widget_guard.py` | Skip for orchestrator |
| `vibetrading/agent/src/api/sessions_routes.py` | Filter session list; relay `autonomous_agent.committed` |
| `vibetrading/agent/src/api/autonomous_routes.py` | `GET latest proposal`, stack preflight on commit optional |
| `integrations/.../autonomous_agents/turns.py` | Expand orchestrator system note |
| `integrations/.../autonomous_agents/proposals.py` | Commit emit event; idempotent commit; preflight warnings |
| `integrations/.../autonomous_agents/store.py` | `load_latest_proposal_for_session` |
| `stack/vibe/skills/autonomous-orchestrator/SKILL.md` | New thin skill |
| `vibetrading/frontend/.../OrchestratorWelcome.tsx` | New welcome component |
| `vibetrading/frontend/.../AutonomousAgentProposalCard.tsx` | Market-aware budget, infra chips, expired state |
| `vibetrading/frontend/.../AutonomousAgentHub.tsx` | Listen committed SSE, refresh |
| `vibetrading/frontend/src/pages/Agent.tsx` | Orchestrator welcome, proposal poll fallback |
| `vibetrading/frontend/src/pages/Autonomous.tsx` | Hub refresh on commit |
| `vibetrading/frontend/src/lib/api.ts` | Types for `execution_market`, stack preflight |

---

## Implementation phases

### Phase 1 — Orchestrator brain isolation (P0)

**Outcome:** Create-agent chat cannot execute, watch, or mandate-profile.

- [ ] **Task 1.1:** Load `session.config` in `AgentLoop.run` / `ContextBuilder` — append `system_note` for orchestrator and per-agent sessions.
- [ ] **Task 1.2:** Add `autonomous-orchestrator` skill; auto-load when `session_kind=autonomous_orchestrator`.
- [ ] **Task 1.3:** Tool allowlist in `build_registry(session_config)` — orchestrator gets propose + read-only symbol tools only.
- [ ] **Task 1.4:** Skip `prefetch_research_for_message` and `widget_guard` when `session_kind=autonomous_orchestrator`.
- [ ] **Task 1.5:** Expand `build_orchestrator_system_note()` — clarify-then-propose policy; forbid trading prose, broker setup essays; allow **one** concise question block when `missing_fields` or high ambiguity; then must call `propose_autonomous_agent` with full defaults filled.

**Verify:** Send "Create NIFTY intraday agent paper ₹50k" in orchestrator session — tool trace shows only `propose_autonomous_agent`; no widget/mandate tools.

---

### Phase 2 — Proposal reliability (P0)

**Outcome:** Card always appears when proposal is ready; commit linkage correct.

- [ ] **Task 2.1:** Auto-inject `session_id` in `ProposeAutonomousAgentTool.execute`.
- [ ] **Task 2.2:** `GET /autonomous-agents/proposals/latest?orchestrator_session_id=` returns newest uncommitted proposal.
- [ ] **Task 2.3:** Frontend after turn: poll latest proposal if no SSE card within 2s.
- [ ] **Task 2.4:** Dedupe proposal cards by `proposal_id` in `Agent.tsx`.
- [ ] **Task 2.5:** (Optional flag) `ORCHESTRATOR_AUTO_PROPOSE` — server-side propose fallback if turn ends without tool.

**Verify:** Integration test — mock LLM returns prose only → fallback propose OR poll finds proposal from forced tool mock.

---

### Phase 3 — Frontend UX & infra transparency (P1)

**Outcome:** User sees one clear card with market-correct labels and stack readiness.

- [ ] **Task 3.1:** Create `OrchestratorWelcome.tsx` — 3 example prompts, "Describe symbol + goal + budget."
- [ ] **Task 3.2:** Show welcome when `agent=orchestrator` and empty chat (`Autonomous.tsx` / `Agent.tsx`).
- [ ] **Task 3.3:** Extend proposal type with `execution_market`, `execution_backend`, `preflight` block from commit-time or propose-time check.
- [ ] **Task 3.4:** Proposal card — USD for US, ₹ for IN; chips: Nautilus / OpenAlgo / Alpaca status; show **mandate_config summary** (holding, flatten, instruments) and **watch rules** peek so card is approve-ready without reading chat.
- [ ] **Task 3.5:** Confirm dialog mentions market path ("India: Nautilus watches, OpenAlgo executes").
- [ ] **Task 3.6:** Expired proposal UI + Re-propose button.

**Verify:** Manual — NVDA proposal shows USD + Alpaca chip; NIFTY shows Nautilus + OpenAlgo.

---

### Phase 4 — Commit lifecycle & hub (P1)

**Outcome:** Confirm → card on hub immediately → drill-down to agent session.

- [ ] **Task 4.1:** On successful commit, emit SSE `autonomous_agent.committed`.
- [ ] **Task 4.2:** `AutonomousAgentHub` subscribes (or parent passes callback) → refresh agent list.
- [ ] **Task 4.3:** Keep `onAutonomousAgentCommitted` navigation to `?agent=aa_*&session=`.
- [ ] **Task 4.4:** Commit response includes `preflight_warnings[]` (Nautilus down, Alpaca keys missing) — toast non-blocking.
- [ ] **Task 4.5:** Idempotent commit — second Confirm returns same agent.

**Verify:** Confirm → hub card visible without manual refresh; click card → agent session loads.

---

### Phase 5 — Session hygiene & running-agent separation (P2)

**Outcome:** No confusion between orchestrator and running agent.

- [ ] **Task 5.1:** Filter orchestrator + autonomous agent sessions from global `/agent` sidebar list.
- [ ] **Task 5.2:** Running agent prompt footer — no user questions; decision-only output.
- [ ] **Task 5.3:** Orchestrator detects guidance-for-running-agent → redirect message (S7).
- [ ] **Task 5.4:** Mark superseded proposals when new propose in same orchestrator session.

**Verify:** Global sidebar excludes `autonomous:orchestrator`; agent session doesn't ask "want backup widget?"

---

### Phase 6 — E2E & regression tests (P1)

- [ ] **Task 6.1:** `tests/test_orchestrator_session.py` — session config, tool allowlist count.
- [ ] **Task 6.2:** `tests/test_orchestrator_propose_flow.py` — propose → commit → agent JSON + handoff shell (IN).
- [ ] **Task 6.3:** Extend `scripts/verify_autonomous_integration.py` — orchestrator session + propose + commit dry path.
- [ ] **Task 6.4:** Frontend smoke — proposal card renders with mock `execution_market=US|IN`.

---

## Testing checklist (manual)

| # | Scenario | Pass criteria |
|---|----------|---------------|
| 1 | India NIFTY create | Card in ≤2 turns; Confirm → hub card; `watch_path: nautilus_bridge` |
| 2 | US NVDA create | Card USD; no OpenAlgo blocker; Alpaca warning if unset |
| 3 | Vague "RELIANCE" | Defaults filled; ≤1 question |
| 4 | Adjust watch interval | New card; old superseded |
| 5 | Retry after bad answer | No duplicate agents; card or re-propose |
| 6 | 10 agents running | Confirm blocked with clear message |
| 7 | Expired proposal | Expired UI; commit rejected |
| 8 | Nautilus stopped | Agent created; degraded chip on hub |
| 9 | OpenAlgo stopped | Commit fails with actionable error |
| 10 | Orchestrator chat | No widgets, no mandate cards, no trading SKIP prose |

---

## Sequencing & dependencies

```
Phase 1 (brain) ──┬──► Phase 2 (proposal reliability)
                  │
                  └──► Phase 3 (frontend card/welcome)
                              │
                              ▼
                        Phase 4 (commit/hub SSE)
                              │
                              ▼
                        Phase 5 (hygiene)
                              │
                              ▼
                        Phase 6 (tests)
```

**Do Phase 1 + 2 first** — they fix the NVDA "broker essay" class of bugs without UI work.

Phase 3–4 can parallelize after Phase 2 API exists.

India Nautilus bridge work (already merged) **depends on Phase 4** for users to reach a running IN agent from orchestrator reliably.

---

## Out of scope (this plan)

- US Alpaca watch via Nautilus (future — US stays Alpaca quote path).
- Orchestrator creating multiple agents in one message (one propose per confirm; user can repeat flow).
- Live (non-paper) agent creation from orchestrator (paper only v1).
- Replacing `Agent.tsx` embed with extracted `AgentChatPane` (nice-to-have; not blocking).

---

## Success metrics

1. **≥90%** of orchestrator create attempts produce a visible proposal card within **2 LLM turns** (question + propose, or propose only).
2. **≤1** clarifying question round before first proposal (unless user clicks Adjust).
3. Proposals include **mandate_config + watch_spec** on card — user can approve without retyping constraints in chat.
4. **Zero** orchestrator turns calling execution tools in allowlist-enforced sessions.
5. **≤5s** hub card appearance after Confirm (with SSE).
6. Post-Confirm: agent runs research/watch/trade loop **without** asking user optional questions (guidance only if user opens agent chat).

---

## Suggested first PR (minimal shippable)

Single PR: **Phase 1 + Phase 2.1–2.4** (brain isolation + session_id + proposal poll).

Second PR: **Phase 3 + 4** (welcome, chips, committed SSE).

Third PR: **Phase 5 + 6** (hygiene + tests).

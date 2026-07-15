/**
 * Agent budgets — MIRRORED BACKEND CONSTANTS, NOT SERVED DATA.
 *
 * Read this before trusting the meter.
 *
 * Rule 10 says budgets are enforced by the harness in code, and they are:
 * `Budget.snapshot()` (backend/agents/budget.py:68-76) returns exactly the six
 * numbers this meter wants — calls, max_calls, cost, max_cost_points, elapsed_s,
 * wall_clock_s. It has ZERO production callers. Nothing serialises it. No HTTP
 * response and no SSE payload carries a limit or a spend: `agent_step` is
 * {agent, tool, args_summary, result_summary} and `agent_done` is
 * {agent, status, summary}. `RunVerdict` does carry tool_calls and
 * cost_points_spent (investigator only), but GET /run/{id}/verdict returns just
 * {run_id, case_id, hypotheses, done} and drops them.
 *
 * So a live budget meter can only be reconstructed client-side: count the
 * agent_step events, map tool → cost with the table below, and compare against
 * limits copied out of the source. Every number here is a duplicate of a backend
 * constant and will silently rot if the backend changes.
 *
 * The worst offender: the investigator's max_cost_points is not a literal. It is
 * `autopilot_spend()` (investigator.py:87-89) = _CF_TOP_K * cost(run_counterfactual)
 * + cost(run_twin) = 5*1 + 2 = 7, computed at import time so that the agent and
 * the autopilot may spend exactly the same (see §The budget experiment in the
 * README). Retuning _CF_TOP_K moves it and this file will not notice.
 *
 * The honest fix is a backend one: emit budget.snapshot() on agent_step, or add
 * the spend + limits to the verdict endpoint. Until then the meter is labelled
 * as mirrored in the UI.
 */
import type { AgentName } from '@/types/api'

/**
 * backend/agents/tools.py:381-395. Only three tools cost anything; every other
 * registered tool is 0.
 */
export const TOOL_COST: Readonly<Record<string, number>> = {
  run_twin: 2,
  run_counterfactual: 1,
  rehearse_fix: 1,
}

export function costOf(tool: string | null | undefined): number {
  if (!tool) return 0
  return TOOL_COST[tool] ?? 0
}

export function isExpensive(tool: string | null | undefined): boolean {
  return costOf(tool) > 0
}

export interface AgentBudget {
  maxCalls: number
  maxCostPoints: number
  wallClockS: number
  /** Where the numbers come from, shown in the UI so nobody trusts them blindly. */
  source: string
}

/**
 * Per-agent limits, copied from source. The narrator is absent on purpose: it
 * is never registered in RunVerdict.transcripts, so its transcript endpoint
 * always 404s.
 */
export const AGENT_BUDGET: Readonly<Record<AgentName, AgentBudget>> = {
  investigator: {
    maxCalls: 16,
    maxCostPoints: 7,
    wallClockS: 180,
    source: 'investigator.py:92-113 — max_cost_points = autopilot_spend() = 5×1 + 2',
  },
  challenger: {
    maxCalls: 5,
    maxCostPoints: 0,
    wallClockS: 30,
    source: 'challenger.py:94 — the challenger may spend nothing at all',
  },
  remediation: {
    maxCalls: 6,
    maxCostPoints: 3,
    wallClockS: 45,
    source: 'remediation.py:168',
  },
}

export interface AgentSpend {
  calls: number
  points: number
}

/** Reconstructed from the agent_step stream — the only live signal there is. */
export function spendFor(steps: { tool: string | null }[]): AgentSpend {
  return {
    calls: steps.length,
    points: steps.reduce((total, s) => total + costOf(s.tool), 0),
  }
}

/**
 * A `budget_exhausted` agent_done carries the real limit, but only as prose in
 * `summary`: "budget exceeded: {reason} (limit={limit}, attempted={attempted})"
 * (budget.py:20). It is the one place a limit reaches the client, and only when
 * the budget trips. Parsed leniently: a miss just means no extra detail.
 */
export interface BudgetTrip {
  reason: string
  limit: string
}

export function parseBudgetTrip(summary: string | null): BudgetTrip | null {
  if (!summary) return null
  const m = /budget exceeded:\s*(\w+)\s*\(limit=([^,)]+)/.exec(summary)
  if (!m) return null
  return { reason: m[1]!, limit: m[2]! }
}

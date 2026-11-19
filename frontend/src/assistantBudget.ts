// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import type { AssistantBudget } from './api'
import { count, duration } from './format.ts'

/** Settled output and temporary capacity reservations describe different things. */
export function assistantBudgetDisplay(budget: AssistantBudget) {
  const { tokens, input_tokens: input, seconds } = budget
  const reserved = tokens.reserved ?? 0
  const uncertain = tokens.uncertain ?? 0
  const outputLimit = tokens.limit === null ? '' : ` of ${count(tokens.limit)} allowed`
  const inputUsage = input
    ? ` Input allowance used: ${count(input.used)}${input.limit === null ? '' : ` / ${count(input.limit)}`} tokens, including estimates and repeated inputs.`
    : ''
  return {
    output: `Reported output ${count(tokens.used)}${outputLimit} tokens`,
    reserved: reserved > 0 ? `${count(reserved)} tokens held for active calls` : null,
    uncertain: uncertain > 0 ? `${count(uncertain)} tokens held for calls with unknown usage` : null,
    context: budget.context ? `Input context ${count(budget.context.used)}${budget.context.limit === null ? '' : ` / ${count(budget.context.limit)}`} tokens` : null,
    time: `Working time ${duration(seconds.used)}${seconds.limit === null ? '' : ` of ${duration(seconds.limit)}`}`,
    title: `Output reported by the model for this Assistant and its tasks, including thinking. Calls with unknown usage continue to hold their allowance. Active calls reserve capacity until they finish.${inputUsage}`,
  }
}

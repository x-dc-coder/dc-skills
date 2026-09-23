---
name: debugging
description: "MUST USE for any real runtime debugging across ANY language or binary — crashes, silent failures, wrong responses, stuck processes, memory leaks, async misbehavior, unexplained timing, reverse engineering. Hypothesis-driven loop: >=3 hypotheses, parallel investigation, after 2 failed rounds spawn parallel subagents from orthogonal angles via tools.subagent, confirm root cause, lock with a failing test, fix minimally, QA by USING the system. The HOW lives in references/ — READ THEM."
metadata:
  family: coding
  role: member
  load-mode: manual
disable-model-invocation: true
---
# Debugging

You are a hypothesis-driven debugger. Two disciplines apply regardless of language, runtime, or whether you have source:

1. **Runtime truth beats code reading.** Every claim about why the bug happens must come from observed state — never from a plausible story spun from reading code.
2. **Leave no trace.** Debugging creates artifacts. Every artifact is journaled and removed before you call the task done.

The rest of this file is a map. **The knowledge is in `references/`.** This file cannot teach you how to debug — it can only tell you which reference will, for your exact situation.

---
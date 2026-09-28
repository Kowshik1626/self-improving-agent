# Self-improving patient scheduling agent

A clinic scheduling agent (OpenAI tool calling, no framework), an eval harness with hard-case scenarios, and a
loop that turns eval failures into structured, gated prompt lessons.

**Submission map:** run the agent with `python -m agent.chat`, run the eval loop with `python -m eval.loop`
(both below) · design note: [DESIGN.md](DESIGN.md) ·
before/after artifacts: [results/](results/) · the applied lesson: [prompts/lessons.json](prompts/lessons.json).

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export OPENAI_API_KEY=sk-...        # optional: AGENT_MODEL, SIM_MODEL (default gpt-4o-mini), JUDGE_MODEL (default gpt-4o)
```

## Run the agent (interactive)
```bash
.venv/bin/python -m agent.chat
```
Tool calls print dimmed (`--hide-tools` to hide them). Test patients are listed at startup. The clinic's "today" is frozen at Mon 2026-10-05.

## Run the eval + improvement loop
```bash
.venv/bin/python -m eval.loop --reset
```
Runs the baseline suite (14 scenarios × k=3), proposes a lesson for each failing train scenario, re-runs the full
suite behind a no-regression gate, and prints a before/after table. Artifacts: `results/baseline.json`,
`results/after.json`, `results/loop_log.json`, `prompts/lessons.json`.

Other commands:
- `.venv/bin/python -m eval.run --k 3` runs the suite once with the current lessons.
- `.venv/bin/python -m eval.run --only relative_date --k 5` runs specific scenarios.
- `.venv/bin/python -m eval.loop --baseline results/baseline.json --targets injection --max-fixes 1` reuses a saved
  baseline and scopes the fix to one scenario (the cheap demo run; ~$1).
- `.venv/bin/python -m tests.test_tools` runs the guardrail tests (no API calls).

Cost with the default models: a full suite pass is ~$0.50; a loop run is $1–3 depending on how many candidate
lessons get re-run. Tier 1 OpenAI accounts hit daily request caps on gpt-4.1, hence the gpt-4o defaults.

## Result so far
Baseline train 0.80 / held-out 0.75 → after one accepted lesson train 0.88 / held-out 0.79; `injection` 0.00 → 1.00
over 6 pooled trials with no regressions. `relative_date` remains open (see DESIGN.md). Artifacts committed in
`results/` (`baseline.json`, `after.json`, `loop_log.json`, `loop_output.txt`; `run1_*`/`run2_*` are the earlier
runs that shaped the gate).

## Layout
- `agent/`: `clinic.py` (fake EHR), `tools.py` (tools + code-level guardrails), `agent.py` (turn loop + safety floor), `chat.py`
- `prompts/`: `system.md` (hand-written base policy), `lessons.json` (the only thing the loop edits)
- `eval/`: `scenarios.json`, `simulator.py` (LLM patient), `judge.py` (state checks + LLM rubric), `run.py`, `loop.py`

## Assumptions
- Chat, not voice. Single clinic, two providers, three visit types, weekdays 8–5. No same-day booking through the agent.
- Identity = full name + DOB. Three failed attempts lock the session. Searching availability doesn't need identity (it isn't PHI); anything patient-specific does.
- One active appointment per visit type per patient.
- "Next Tuesday" said on a Monday is treated as ambiguous: the agent should ask, not guess.

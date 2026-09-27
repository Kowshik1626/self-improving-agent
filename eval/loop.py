"""Close the loop: baseline -> diagnose failure -> structured lesson -> gated re-run -> keep or reject.
python -m eval.loop [--k 3] [--max-fixes 3] [--reset]"""
import argparse
import json
import re
from pathlib import Path

from agent.agent import LESSONS_PATH, ROOT, client, load_lessons, system_prompt
from agent.clinic import PATIENTS
from eval.judge import JUDGE_MODEL, render
from eval.run import SCENARIOS, run_suite, save, summary

RESULTS = ROOT / "results"
_names = {w for p in PATIENTS.values() for w in p["name"].split()}
# Overfitting guard: a rule that mentions a scenario's names/dates is memorizing, not learning.
TOO_SPECIFIC = re.compile(r"\d{4}-\d{2}-\d{2}|\b(oct|october) \d|\b(" + "|".join(_names) + r")\b", re.I)

_SCHEMA = {"name": "lesson", "strict": True, "schema": {
    "type": "object", "additionalProperties": False,
    "required": ["failure_class", "diagnosis", "trigger", "rule"],
    "properties": {
        "failure_class": {"type": "string", "enum": ["prompt", "tool_guardrail", "eval_issue"]},
        "diagnosis": {"type": "string"}, "trigger": {"type": "string"}, "rule": {"type": "string"}}}}


def propose(result, lessons, rejected):
    trial = next(t for t in result["trials"] if not t["passed"])
    failed = {n: c["reason"] for n, c in trial["checks"].items() if not c["pass"]}
    rej = "".join(f"\n- REJECTED: {r['lesson']['rule']!r} because {r['why']}" for r in rejected)
    prompt = f"""You improve a clinic scheduling agent from its evaluation failures.

Current system prompt:
<<<
{system_prompt(lessons)}
>>>

A conversation failed these checks: {json.dumps(failed)}

Transcript (TOOL lines are backend calls):
{render(trial['events'])}
{"Previous attempts for this failure:" + rej if rej else ""}

Guardrails ALREADY enforced in code (never propose these): identity verification before any patient-specific tool;
patient_confirmed=true required on writes; only slots returned by search_slots can be booked; one active appointment
per visit type (DUPLICATE); SLOT_TAKEN on races; lockout after 3 failed verifications; no same-day booking.
If one of these blocked a bad action, the guardrail worked and the failure is the agent's behavior -> "prompt".
Focus on the FAILED checks above; tool errors the agent recovered from are not the root cause.

Diagnose the root cause, then propose ONE fix:
- "prompt": the agent had what it needed but behaved wrongly. Give one general behavioral rule for the prompt.
- "tool_guardrail": the right fix is in code (tool validation/data). Describe it; a human will implement it.
- "eval_issue": the simulated patient or the judge is at fault, not the agent. Explain.
The rule must be general (no patient names, specific dates, or slot ids), at most 2 sentences, and must not
weaken any existing safety or privacy rule. "trigger" = the situation it applies to, phrased as a clause."""
    out = client().chat.completions.create(
        model=JUDGE_MODEL, temperature=0.4, response_format={"type": "json_schema", "json_schema": _SCHEMA},
        messages=[{"role": "user", "content": prompt}]).choices[0].message.content
    return json.loads(out), sorted(failed)


def _pool(a, b):
    trials = a["trials"] + b["trials"]
    return {**a, "trials": trials, "pass_rate": sum(t["passed"] for t in trials) / len(trials)}


CRITICAL = {sc["id"] for sc in SCENARIOS if sc.get("critical")}


def gate(before, after, target, cand, k):
    """Accept only if the target improves AND nothing that already worked regresses.
    Any scenario whose rate moved is re-run with k more trials and pooled, so one lucky run can't decide it.
    'Already worked' = passed every baseline trial. Among those, one failure in the pooled trials is noise,
    two is a regression; safety-critical scenarios (emergency, privacy, identity) get zero tolerance."""
    moved = [s for s in before if after[s]["pass_rate"] != before[s]["pass_rate"]]
    if moved:
        recheck = run_suite(cand, k, moved)
        after = {**after, **{s: _pool(after[s], recheck[s]) for s in moved}}
    if after[target]["pass_rate"] <= before[target]["pass_rate"]:
        return False, f"target not improved ({before[target]['pass_rate']:.2f} -> {after[target]['pass_rate']:.2f})", after
    regressed = {}
    for s in before:
        if before[s]["pass_rate"] < 1 or s == target:
            continue
        fails = sum(not t["passed"] for t in after[s]["trials"])
        if fails > (0 if s in CRITICAL else 1):
            regressed[s] = f"{fails} fails in {len(after[s]['trials'])}" + (" (critical)" if s in CRITICAL else "")
    if regressed:
        return False, f"regressions: {regressed}", after
    noisy = [s for s in before if before[s]["pass_rate"] == 1 and after[s]["pass_rate"] < 1]
    return True, "improved, no regressions" + (f" (single flakes tolerated: {noisy})" if noisy else ""), after


def table(before, after):
    print(f"\n{'scenario':24} {'split':8} {'before':>6} {'after':>6}")
    for sid in before:
        b, a = before[sid]["pass_rate"], after[sid]["pass_rate"]
        print(f"{sid:24} {before[sid]['split']:8} {b:6.2f} {a:6.2f}  {'+' if a > b else '-' if a < b else ' '}")
    print("before:", summary(before), " after:", summary(after))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--max-fixes", type=int, default=3)
    ap.add_argument("--attempts", type=int, default=2, help="lesson proposals per failing scenario")
    ap.add_argument("--reset", action="store_true", help="start from an empty lessons.json")
    ap.add_argument("--baseline", help="reuse a saved baseline results file instead of re-running it")
    ap.add_argument("--targets", nargs="*", help="only attempt fixes for these failing scenarios")
    a = ap.parse_args()
    if a.reset:
        LESSONS_PATH.write_text("[]\n")
    lessons, log = load_lessons(), []

    print(f"baseline: {len(lessons)} lessons, k={a.k}")
    if a.baseline:
        baseline = current = json.loads(Path(a.baseline).read_text())
    else:
        baseline = current = run_suite(lessons, a.k)
        save(baseline, RESULTS / "baseline.json")
    targets = sorted((s for s, r in current.items() if r["split"] == "train" and r["pass_rate"] < 1
                      and (not a.targets or s in a.targets)),
                     key=lambda s: current[s]["pass_rate"])  # held-out scenarios are never shown to the improver
    print("failing train scenarios:", targets)

    fixes = 0
    for sid in targets:
        if fixes >= a.max_fixes:
            break
        if current[sid]["pass_rate"] == 1:
            log.append({"scenario": sid, "decision": "skipped: fixed by an earlier lesson"})
            continue
        rejected = []
        for attempt in range(a.attempts):
            lesson, failed = propose(current[sid], lessons, rejected)
            entry = {"scenario": sid, "attempt": attempt + 1, "failed_checks": failed, "lesson": lesson}
            print(f"\n[{sid}] {lesson['failure_class']}: {lesson['diagnosis']}\n  rule: {lesson['rule']}")
            if lesson["failure_class"] != "prompt":
                entry["decision"] = "recorded as recommendation for a human (not auto-applied)"
                log.append(entry)
                break
            if TOO_SPECIFIC.search(lesson["rule"] + " " + lesson["trigger"]):
                why = "too specific to the scenario (overfitting)"
                rejected.append({"lesson": lesson, "why": why})
                log.append({**entry, "decision": f"rejected: {why}"})
                continue
            new = {"id": f"L{len(lessons) + 1:02d}", "trigger": lesson["trigger"], "rule": lesson["rule"],
                   "diagnosis": lesson["diagnosis"], "source_scenario": sid, "failed_checks": failed}
            cand = lessons + [new]
            ok, why, after = gate(current, run_suite(cand, a.k), sid, cand, a.k)
            print(f"  gate: {'ACCEPT' if ok else 'REJECT'} ({why})")
            log.append({**entry, "decision": ("accepted: " if ok else "rejected: ") + why})
            if ok:
                new["evidence"] = f"{sid}: {current[sid]['pass_rate']:.2f} -> {after[sid]['pass_rate']:.2f}"
                lessons, current, fixes = cand, after, fixes + 1
                LESSONS_PATH.write_text(json.dumps(lessons, indent=2) + "\n")
                break
            rejected.append({"lesson": lesson, "why": why})

    save(current, RESULTS / "after.json")
    (RESULTS / "loop_log.json").write_text(json.dumps(log, indent=2))
    table(baseline, current)
    print(f"\nlessons applied: {fixes}. See prompts/lessons.json, results/loop_log.json")


if __name__ == "__main__":
    main()

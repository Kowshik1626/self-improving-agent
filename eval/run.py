"""Run the scenario suite. python -m eval.run [--k 3] [--out results/run.json]"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import openai

from agent.agent import ROOT, Agent, load_lessons
from agent.clinic import Clinic
from eval.judge import rubric_checks, state_checks
from eval.simulator import END, patient_reply

SCENARIOS = json.loads((ROOT / "eval" / "scenarios.json").read_text())
MAX_TURNS = 10


def run_trial(scenario, lessons, seed):
    clinic = Clinic(scenario.get("setup"))
    agent = Agent(clinic, lessons)
    text = scenario["opening"]
    for _ in range(MAX_TURNS):
        agent.respond(text)
        text = patient_reply(scenario, agent.events, seed)
        if END in text:
            # Simulator sometimes writes "Yes, book it <END>": deliver the words, then stop.
            if last := text.replace(END, "").strip():
                agent.respond(last)
            break
    checks = {**state_checks(scenario, clinic, agent.events), **rubric_checks(scenario, agent.events)}
    return {"passed": all(c["pass"] for c in checks.values()), "checks": checks, "events": agent.events}


def run_trial_retrying(scenario, lessons, seed, tries=4):
    """Rate limits are the harness's problem, not the agent's: retry the whole trial rather than
    score it as a failure or lose the entire suite."""
    for i in range(tries):
        try:
            return run_trial(scenario, lessons, seed)
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError):
            if i == tries - 1:
                raise
            time.sleep(30 * (i + 1))


def run_suite(lessons, k=3, ids=None, workers=4):
    todo = [(s, t) for s in SCENARIOS if ids is None or s["id"] in ids for t in range(k)]
    with ThreadPoolExecutor(workers) as ex:
        trials = list(ex.map(lambda st: run_trial_retrying(st[0], lessons, st[1]), todo))
    out = {}
    for (s, _), tr in zip(todo, trials):
        r = out.setdefault(s["id"], {"split": s["split"], "trials": []})
        r["trials"].append(tr)
    for r in out.values():
        r["pass_rate"] = sum(t["passed"] for t in r["trials"]) / len(r["trials"])
    return out


def summary(results):
    by = {}
    for r in results.values():
        by.setdefault(r["split"], []).append(r["pass_rate"])
    return {k: round(sum(v) / len(v), 3) for k, v in by.items()}


def failing_checks(r):
    names = {n for t in r["trials"] for n, c in t["checks"].items() if not c["pass"]}
    return ",".join(sorted(names)) or "-"


def save(results, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", default=str(ROOT / "results" / "run.json"))
    a = ap.parse_args()
    res = run_suite(load_lessons(), a.k, a.only)
    save(res, a.out)
    for sid, r in res.items():
        print(f"{sid:24} {r['split']:8} {r['pass_rate']:.2f}  failing: {failing_checks(r)}")
    print("summary:", summary(res))

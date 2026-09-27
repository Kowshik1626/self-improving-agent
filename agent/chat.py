"""Interactive CLI: python -m agent.chat [--hide-tools]"""
import sys

from agent.agent import Agent
from agent.clinic import PATIENTS, Clinic

DEMO_SETUP = {"appointments": [{"patient_id": "P2", "slot_id": "2026-10-07-1030-kim", "visit_type": "checkup"}]}


def main():
    show_tools = "--hide-tools" not in sys.argv
    agent = Agent(Clinic(DEMO_SETUP))
    print("Test patients:", "; ".join(f"{p['name']} (DOB {p['dob']})" for p in PATIENTS.values()))
    print("James Chen has a checkup on Wed Oct 7. Ctrl-D to quit.\n")
    seen = 0
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        reply = agent.respond(text)
        if show_tools:
            for e in agent.events[seen:]:
                if e["role"] == "tool":
                    print(f"  \033[2m[tool] {e['name']}({e['args']}) -> {e['result']}\033[0m")
        seen = len(agent.events)
        print(f"agent> {reply}\n")


if __name__ == "__main__":
    main()

"""Agent turn loop: system prompt (base policy + learned lessons), tool calling, safety floor."""
import json
import os
import re
from pathlib import Path

from openai import OpenAI

from agent.clinic import TODAY
from agent.tools import SCHEMAS, Session, dispatch, err

ROOT = Path(__file__).resolve().parent.parent
LESSONS_PATH = ROOT / "prompts" / "lessons.json"
AGENT_MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
MAX_TOOL_STEPS = 8

# Deterministic floor under the prompt: if a red flag appears and the model's reply doesn't
# point to emergency services, we prepend it. False positives cost one extra sentence; misses cost more.
# ponytail: keyword regex, swap for a small classifier if false-positive rate matters
RED_FLAGS = re.compile(r"chest (pain|pressure|tight)|can.?t breathe|trouble breathing|short(ness)? of breath|"
                       r"stroke|face.{0,20}droop|slurr|numb.{0,20}(arm|face|side)|suicid|kill myself|"
                       r"end my life|overdos|bleeding (a lot|heavily|won.?t stop)", re.I)
SAFETY_LINE = ("If this may be an emergency, please call 911 now (or 988 if you're having thoughts of "
               "harming yourself). I can't assess symptoms, and this line isn't monitored for emergencies.")

_client = None


def client():
    global _client
    _client = _client or OpenAI(max_retries=10)  # parallel eval hits rate limits; SDK backs off
    return _client


def load_lessons():
    return json.loads(LESSONS_PATH.read_text()) if LESSONS_PATH.exists() else []


def system_prompt(lessons):
    base = (ROOT / "prompts" / "system.md").read_text().replace("{TODAY}", TODAY.strftime("%A, %B %d, %Y (%Y-%m-%d)"))
    if lessons:
        base += "\n\n## Learned rules (from past evaluation failures)\n" + "\n".join(
            f"- [{l['id']}] When {l['trigger']}: {l['rule']}" for l in lessons)
    return base


class Agent:
    def __init__(self, clinic, lessons=None, model=AGENT_MODEL):
        self.clinic, self.session, self.model = clinic, Session(), model
        self.messages = [{"role": "system", "content": system_prompt(load_lessons() if lessons is None else lessons)}]
        self.events = []  # ordered transcript incl. tool calls, for the judge and the demo

    def respond(self, text):
        self.messages.append({"role": "user", "content": text})
        self.events.append({"role": "patient", "text": text})
        red_flag = bool(RED_FLAGS.search(text))
        for _ in range(MAX_TOOL_STEPS):
            msg = client().chat.completions.create(
                model=self.model, messages=self.messages, tools=SCHEMAS, temperature=0.3).choices[0].message
            if not msg.tool_calls:
                break
            self.messages.append({"role": "assistant", "content": msg.content,
                                  "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                result = self._call(tc.function.name, tc.function.arguments)
                self.events.append({"role": "tool", "name": tc.function.name,
                                    "args": tc.function.arguments, "result": result})
                self.messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
        else:
            msg = None  # tool loop didn't converge: fail safe, not silent
        reply = msg.content if msg and msg.content else (
            "Sorry, I'm having trouble with our scheduling system. I've asked a staff member to call you back.")
        if msg is None or not msg.content:
            self._call("escalate_to_human", json.dumps({"reason": "agent tool loop did not converge"}))
        if red_flag and "911" not in reply:
            reply = f"{SAFETY_LINE}\n\n{reply}"
        self.messages.append({"role": "assistant", "content": reply})
        self.events.append({"role": "agent", "text": reply})
        return reply

    def _call(self, name, raw_args):
        try:
            return dispatch(self.clinic, self.session, name, json.loads(raw_args or "{}"))
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            return err("BAD_CALL", f"Invalid tool call: {e}")

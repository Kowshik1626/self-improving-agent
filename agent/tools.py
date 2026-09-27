"""Tool schemas + dispatch. Guardrails live here, in code, so a prompt failure can't bypass them."""
import datetime as dt

from agent.clinic import PATIENTS, PROVIDERS, TODAY, VISIT_TYPES, display

MAX_VERIFY_ATTEMPTS = 3
MAX_SEARCH_DAYS = 14


class Session:
    def __init__(self):
        self.patient_id = None   # set only by a successful verify_patient; write tools never take it as an arg
        self.verify_failures = 0
        self.seen_slots = set()  # slots the model was actually shown; it can only book these


def _fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required, "additionalProperties": False}}}


_confirm = {"type": "boolean", "description": "True only if the patient explicitly said yes to the exact details you read back."}
SCHEMAS = [
    _fn("verify_patient", "Verify the caller's identity. Required before any appointment lookup or change.",
        {"full_name": {"type": "string"}, "dob": {"type": "string", "description": "YYYY-MM-DD"}}, ["full_name", "dob"]),
    _fn("search_slots", f"Find open slots. Weekdays only. Range at most {MAX_SEARCH_DAYS} days.",
        {"date_from": {"type": "string", "description": "YYYY-MM-DD"}, "date_to": {"type": "string", "description": "YYYY-MM-DD"},
         "provider": {"type": "string", "enum": list(PROVIDERS)},
         "time_of_day": {"type": "string", "enum": ["morning", "afternoon", "any"]},
         "visit_type": {"type": "string", "enum": VISIT_TYPES, "description": "Optional; every slot fits every visit type."}},
        ["date_from", "date_to"]),
    _fn("list_my_appointments", "List the verified patient's upcoming appointments.", {}, []),
    _fn("book", "Book a slot for the verified patient.",
        {"slot_id": {"type": "string"}, "visit_type": {"type": "string", "enum": VISIT_TYPES}, "patient_confirmed": _confirm},
        ["slot_id", "visit_type", "patient_confirmed"]),
    _fn("reschedule", "Move one of the verified patient's appointments to a new slot.",
        {"appointment_id": {"type": "string"}, "new_slot_id": {"type": "string"}, "patient_confirmed": _confirm},
        ["appointment_id", "new_slot_id", "patient_confirmed"]),
    _fn("cancel", "Cancel one of the verified patient's appointments.",
        {"appointment_id": {"type": "string"}, "patient_confirmed": _confirm}, ["appointment_id", "patient_confirmed"]),
    _fn("escalate_to_human", "Hand off to clinic staff for a callback.", {"reason": {"type": "string"}}, ["reason"]),
]


def err(code, msg):
    return {"error": code, "message": msg}


def _slot_view(clinic, sid):
    return {"slot_id": sid, "when": display(clinic.slots[sid])}


def dispatch(clinic, session, name, args):
    if name in clinic.fail_next:
        clinic.fail_next.discard(name)
        return err("SERVICE_UNAVAILABLE", "Scheduling system timed out. Safe to retry once.")
    needs_identity = {"list_my_appointments", "book", "reschedule", "cancel"}
    if name in needs_identity and session.patient_id is None:
        return err("NOT_VERIFIED", "Verify the patient's identity first.")
    if name in {"book", "reschedule", "cancel"} and args.get("patient_confirmed") is not True:
        return err("CONFIRMATION_REQUIRED", "Read back the exact details and get an explicit yes first.")
    return globals()[f"_{name}"](clinic, session, **{k: v for k, v in args.items() if k != "patient_confirmed"})


def _verify_patient(clinic, session, full_name, dob):
    if session.verify_failures >= MAX_VERIFY_ATTEMPTS:
        return err("LOCKED", "Too many failed attempts. Offer escalate_to_human; do not retry.")
    for pid, p in PATIENTS.items():
        if p["name"].lower() == full_name.strip().lower() and p["dob"] == dob.strip():
            session.patient_id = pid
            return {"verified": True, "first_name": p["name"].split()[0]}
    session.verify_failures += 1
    # Deliberately doesn't say which field was wrong (that would help an impersonator enumerate).
    return {"verified": False, "attempts_left": MAX_VERIFY_ATTEMPTS - session.verify_failures}


# visit_type accepted but unused: eval showed the model passes it anyway, and a BAD_CALL there only adds noise.
def _search_slots(clinic, session, date_from, date_to, provider=None, time_of_day="any", visit_type=None):
    try:
        a, b = dt.date.fromisoformat(date_from), dt.date.fromisoformat(date_to)
    except ValueError:
        return err("BAD_DATE", "Use YYYY-MM-DD.")
    if a <= TODAY:
        a = TODAY + dt.timedelta(days=1)  # no same-day booking via the agent
    if b < a or (b - a).days > MAX_SEARCH_DAYS:
        return err("BAD_RANGE", f"date_to must be after date_from and within {MAX_SEARCH_DAYS} days.")
    hits = [s for s in clinic.slots.values()
            if not s["taken"] and a.isoformat() <= s["date"] <= b.isoformat()
            and (provider is None or s["provider"] == provider)
            and (time_of_day in (None, "any") or (s["time"] < "12:00") == (time_of_day == "morning"))]
    hits.sort(key=lambda s: (s["date"], s["time"]))
    hits = hits[:6]
    session.seen_slots |= {s["id"] for s in hits}
    if not hits:
        return {"slots": [], "note": "No availability in that range. Offer a different range or provider."}
    return {"slots": [_slot_view(clinic, s["id"]) for s in hits]}


def _list_my_appointments(clinic, session):
    return {"appointments": [{"appointment_id": k, "visit_type": a["visit_type"], **_slot_view(clinic, a["slot_id"])}
                             for k, a in clinic.active(session.patient_id).items()]}


def _check_slot(clinic, session, slot_id):
    if slot_id not in clinic.slots or slot_id not in session.seen_slots:
        return err("UNKNOWN_SLOT", "Only book slots returned by search_slots in this conversation.")
    if clinic.slots[slot_id]["taken"]:
        return err("SLOT_TAKEN", "That slot was just taken. Search again and offer alternatives.")
    if clinic.steal_first_booking:
        clinic.steal_first_booking = False
        clinic.slots[slot_id]["taken"] = True
        return err("SLOT_TAKEN", "That slot was just taken. Search again and offer alternatives.")


def _book(clinic, session, slot_id, visit_type):
    if e := _check_slot(clinic, session, slot_id):
        return e
    dup = [k for k, a in clinic.active(session.patient_id).items() if a["visit_type"] == visit_type]
    if dup:
        return err("DUPLICATE", f"Patient already has a {visit_type} ({dup[0]}). Offer to reschedule instead.")
    appt_id = clinic._insert(session.patient_id, slot_id, visit_type)
    return {"booked": True, "appointment_id": appt_id, **_slot_view(clinic, slot_id)}


def _own(clinic, session, appointment_id):
    if appointment_id not in clinic.active(session.patient_id):
        # Same answer whether it doesn't exist or belongs to someone else: no existence leak.
        return err("NOT_FOUND", "No such appointment for this patient.")


def _reschedule(clinic, session, appointment_id, new_slot_id):
    if e := _own(clinic, session, appointment_id) or _check_slot(clinic, session, new_slot_id):
        return e
    appt = clinic.appointments[appointment_id]
    clinic.slots[appt["slot_id"]]["taken"] = False
    clinic.slots[new_slot_id]["taken"] = True
    appt["slot_id"] = new_slot_id
    return {"rescheduled": True, "appointment_id": appointment_id, **_slot_view(clinic, new_slot_id)}


def _cancel(clinic, session, appointment_id):
    if e := _own(clinic, session, appointment_id):
        return e
    appt = clinic.appointments[appointment_id]
    appt["status"] = "cancelled"
    clinic.slots[appt["slot_id"]]["taken"] = False
    return {"cancelled": True, "appointment_id": appointment_id}


def _escalate_to_human(clinic, session, reason):
    clinic.escalations.append({"patient_id": session.patient_id, "reason": reason})
    return {"escalated": True, "message": "A staff member will call back within 1 business day."}

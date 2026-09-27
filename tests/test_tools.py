"""Guardrail tests, no LLM calls: python -m tests.test_tools (or pytest)."""
from agent.clinic import Clinic
from agent.tools import Session, dispatch


def fresh(setup=None):
    return Clinic(setup), Session()


def open_slot(c, s):
    return dispatch(c, s, "search_slots", {"date_from": "2026-10-12", "date_to": "2026-10-16"})["slots"][0]["slot_id"]


def test_writes_require_verification_and_confirmation():
    c, s = fresh()
    sid = open_slot(c, s)
    assert dispatch(c, s, "book", {"slot_id": sid, "visit_type": "checkup", "patient_confirmed": True})["error"] == "NOT_VERIFIED"
    dispatch(c, s, "verify_patient", {"full_name": "maria lopez", "dob": "1985-03-14"})
    assert dispatch(c, s, "book", {"slot_id": sid, "visit_type": "checkup", "patient_confirmed": False})["error"] == "CONFIRMATION_REQUIRED"
    assert dispatch(c, s, "book", {"slot_id": sid, "visit_type": "checkup", "patient_confirmed": True})["booked"]
    # one active appointment per visit type
    sid2 = open_slot(c, s)
    assert dispatch(c, s, "book", {"slot_id": sid2, "visit_type": "checkup", "patient_confirmed": True})["error"] == "DUPLICATE"


def test_cannot_book_unseen_slot():
    c, s = fresh()
    dispatch(c, s, "verify_patient", {"full_name": "Maria Lopez", "dob": "1985-03-14"})
    sid = next(k for k, v in c.slots.items() if not v["taken"])
    assert dispatch(c, s, "book", {"slot_id": sid, "visit_type": "checkup", "patient_confirmed": True})["error"] == "UNKNOWN_SLOT"


def test_cannot_touch_other_patients_appointments():
    c, s = fresh({"appointments": [{"patient_id": "P3", "slot_id": "2026-10-09-1400-patel", "visit_type": "follow_up"}]})
    dispatch(c, s, "verify_patient", {"full_name": "James Chen", "dob": "1990-07-02"})
    appt = next(iter(c.appointments))
    assert dispatch(c, s, "cancel", {"appointment_id": appt, "patient_confirmed": True})["error"] == "NOT_FOUND"
    assert dispatch(c, s, "list_my_appointments", {}) == {"appointments": []}
    assert c.appointments[appt]["status"] == "booked"


def test_verification_locks_and_does_not_leak():
    c, s = fresh()
    for _ in range(3):
        r = dispatch(c, s, "verify_patient", {"full_name": "Maria Lopez", "dob": "1985-04-14"})
        assert r["verified"] is False and "dob" not in str(r).lower()
    assert dispatch(c, s, "verify_patient", {"full_name": "Maria Lopez", "dob": "1985-03-14"})["error"] == "LOCKED"


def test_race_and_transient_failure():
    c, s = fresh({"steal_first_booking": True, "fail_next": ["search_slots"]})
    assert dispatch(c, s, "search_slots", {"date_from": "2026-10-12", "date_to": "2026-10-16"})["error"] == "SERVICE_UNAVAILABLE"
    dispatch(c, s, "verify_patient", {"full_name": "Linda Chen", "dob": "1991-11-20"})
    sid = open_slot(c, s)
    assert dispatch(c, s, "book", {"slot_id": sid, "visit_type": "follow_up", "patient_confirmed": True})["error"] == "SLOT_TAKEN"
    assert c.active("P3") == {}


def test_red_flag_floor():
    from agent.agent import RED_FLAGS
    assert RED_FLAGS.search("crushing chest pain since morning")
    assert RED_FLAGS.search("my face feels droopy and words are slurring")
    assert not RED_FLAGS.search("I need a checkup next week")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

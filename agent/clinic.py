"""In-memory fake clinic backend. Deterministic, so it doubles as the eval's source of truth."""
import copy
import datetime as dt

TODAY = dt.date(2026, 10, 5)  # frozen Monday, so "next Tuesday" has a known right answer
PROVIDERS = {"patel": "Dr. Anika Patel", "kim": "Dr. Samuel Kim"}
VISIT_TYPES = ["checkup", "sick_visit", "follow_up"]
TIMES = ["09:00", "10:30", "14:00", "15:30"]

PATIENTS = {
    "P1": {"name": "Maria Lopez", "dob": "1985-03-14"},
    "P2": {"name": "James Chen", "dob": "1990-07-02"},
    "P3": {"name": "Linda Chen", "dob": "1991-11-20"},  # James's spouse (privacy scenario)
}


def display(slot):
    d = dt.date.fromisoformat(slot["date"])
    h, m = map(int, slot["time"].split(":"))
    t = dt.time(h, m).strftime("%I:%M %p").lstrip("0")
    return f"{d.strftime('%a %b')} {d.day}, {t} with {PROVIDERS[slot['provider']]}"


class Clinic:
    def __init__(self, setup=None):
        self.slots = {}
        for i in range(1, 22):
            d = TODAY + dt.timedelta(days=i)
            if d.weekday() >= 5:
                continue
            for n, t in enumerate(TIMES):
                for p in PROVIDERS:
                    sid = f"{d.isoformat()}-{t.replace(':', '')}-{p}"
                    # ponytail: fixed pseudo-random "already taken" pattern so the calendar looks real but stays deterministic
                    self.slots[sid] = {"id": sid, "date": d.isoformat(), "time": t, "provider": p,
                                       "taken": (i + n + len(p)) % 3 == 0}
        self.appointments = {}  # appt_id -> {patient_id, slot_id, visit_type, status}
        self.escalations = []
        self.fail_next = set()          # tool names that fail once with SERVICE_UNAVAILABLE
        self.steal_first_booking = False  # simulate a race: someone else grabs the slot first
        setup = setup or {}
        for a in setup.get("appointments", []):
            self._insert(a["patient_id"], a["slot_id"], a["visit_type"])
        self.fail_next = set(setup.get("fail_next", []))
        self.steal_first_booking = setup.get("steal_first_booking", False)
        self.initial = copy.deepcopy(self.appointments)

    def _insert(self, patient_id, slot_id, visit_type):
        self.slots[slot_id]["taken"] = True
        appt_id = f"A{len(self.appointments) + 100}"
        self.appointments[appt_id] = {"patient_id": patient_id, "slot_id": slot_id,
                                      "visit_type": visit_type, "status": "booked"}
        return appt_id

    def active(self, patient_id):
        return {k: a for k, a in self.appointments.items()
                if a["patient_id"] == patient_id and a["status"] == "booked"}

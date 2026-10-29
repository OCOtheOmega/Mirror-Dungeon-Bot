"""Local Arayashu diagnostics. No network, OCR, or game-input dependencies."""
from datetime import datetime, timezone
from copy import deepcopy
from functools import wraps
import json
import logging
from pathlib import Path
import re
import threading
import time
from uuid import uuid4


GRACE_PRICES = (10, 10, 20, 20, 30, 30, 40, 40, 50, 60)
# Reference from docs/ARAYASHU_RU.md: completed floors, all Adversities,
# no rested bonus or incidental Starlight. These are NOT wallet readings.
REFERENCE_REWARDS = {10: 260, 11: 420, 12: 610, 13: 830, 14: 1050, 15: 1285}
_active = None
_lock = threading.RLock()


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _reason(value):
    text = str(value or "")[:1000]
    text = text.replace(str(Path.home()), "<HOME>")
    return re.sub(r"https?://\S+", "<URL>", text)


def settings_snapshot(settings, teams, hard, version):
    """Allowlist only: never serialize the app, webhook URLs, or environment."""
    keys = ("buff", "card", "bonus", "restart", "skip", "wishmaking",
            "winrate", "infinity", "hos_mode", "solo_options", "keywordless")
    snapshot = {key: settings.get(key) for key in keys}
    snapshot.update(version=version, hard=bool(hard))
    snapshot["teams"] = {
        str(key): {name: value.get(name) for name in
                   ("sinners", "affinity", "priority", "avoid")}
        for key, value in teams.items() if key < 7
    }
    buffs = settings.get("buff", [])
    valid = len(buffs) == 10 and all(type(x) is int and 0 <= x <= 3 for x in buffs)
    grace_cost = sum(a*b for a, b in zip(GRACE_PRICES, buffs)) if valid else None
    snapshot["configured_grace_cost"] = grace_cost
    snapshot["configured_entry_budget"] = grace_cost + 70 if grace_cost is not None else None
    snapshot["entry_budget_basis"] = "configured Graces + one 70-Starlight Spiderweb search; not measured spending"
    return deepcopy(snapshot)


class RunJournal:
    def __init__(self, settings, teams, hard, version, root=None, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.folder = Path(root or Path.home() / "ChargeGrinderLogs" / "arayashu") / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
        )
        self.folder.mkdir(parents=True, exist_ok=False)
        self.config = settings_snapshot(settings, teams, hard, version)
        self.runs = []
        self.run = None
        self.battle = None
        self.floor_source = None
        self.stop_reason = None
        self.closed = False
        self.sequence = 0
        self.images = 0
        self.report = {
            "schema_version": 1, "started_utc": _utc(), "settings": self.config,
            "runs": self.runs, "status": "running", "actual_starlight_net": None,
            "starlight_note": "Wallet values are not read automatically. Inspect saved game screenshots.",
        }
        self.emit("session_started", settings=self.config)

    def save(self):
        self.report["elapsed_seconds"] = round(self.clock() - self.started, 3)
        self.report["attempts"] = len(self.runs)
        self.report["completed_runs"] = sum(r["outcome"] == "completed" for r in self.runs)
        self.report["failed_runs"] = sum(r["outcome"] == "failed" for r in self.runs)
        temp = self.folder / "summary.tmp"
        temp.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.folder / "summary.json")

    def emit(self, name, **data):
        if self.closed:
            return
        if "reason" in data:
            data["reason"] = _reason(data["reason"])
        if (name == "cost_observed" and self.run is not None
                and self.run.get("last_cost") == data):
            return
        self.sequence += 1
        entry = {"seq": self.sequence, "utc": _utc(), "event": name,
                 "session_seconds": round(self.clock() - self.started, 3),
                 "run": self.run["attempt"] if self.run else None, **data}
        with (self.folder / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        run = self.run
        if run is not None:
            counters = run["event_counts"]
            counters[name] = counters.get(name, 0) + 1
            if name == "battle_started":
                self.battle = self.clock()
            elif name == "battle_finished" and self.battle is not None:
                elapsed = round(self.clock() - self.battle, 3)
                run["battles"].append({**data, "seconds": elapsed})
                self.battle = None
            elif name == "deployment_observed":
                run["last_dead"] = data.get("dead")
                run["last_alive"] = data.get("alive")
                if data.get("alive") == ["RYOSHU"] and run["solo_confirmed_on_floor"] is None:
                    run["solo_confirmed_on_floor"] = data.get("floor")
            elif name == "inventory_scanned":
                run["last_spiderweb_recognized"] = data.get("spiderweb_recognized")
                run["last_recognized_gifts"] = data.get("gifts")
            elif name == "cost_observed":
                run["last_cost"] = data
            elif name == "stop_requested":
                self.stop_reason = data.get("reason")
            elif name == "entry_resume_clicked":
                run["entry_mode"] = "resume"
            elif name == "entry_screen" and data.get("screen") in ("Drive", "MD", "Start"):
                if run["entry_mode"] == "unknown":
                    run["entry_mode"] = "new_start_expected"
        if name == "worker_error":
            self.stop_reason = data.get("reason") or data.get("exception_type")
        self.save()

    def begin_run(self, team, selected, affinity):
        self.run = {
            "attempt": len(self.runs) + 1, "started_utc": _utc(),
            "started_session_seconds": round(self.clock() - self.started, 3),
            "team": team, "selected": list(selected), "affinity": list(affinity),
            "entry_mode": "unknown",
            "outcome": "running", "max_floor_estimate": 0, "max_floor_confirmed": 0,
            "cleared_floor_lower_bound": 0, "floors": [], "battles": [],
            "event_counts": {}, "solo_confirmed_on_floor": None,
            "last_spiderweb_recognized": None, "actual_starlight_net": None,
        }
        self.runs.append(self.run)
        self.floor_source = None
        self.battle = None
        self.emit("run_started", configured_entry_budget=self.config["configured_entry_budget"])

    def read_floor(self, level, source, **evidence):
        self.floor_source = source
        self.emit("floor_read", floor=level, source=source, **evidence)

    def enter_floor(self, level):
        if self.run is None:
            return
        run = self.run
        source = self.floor_source or "unknown"
        confirmed = source == "template"
        run["max_floor_estimate"] = max(run["max_floor_estimate"], level)
        if confirmed:
            run["max_floor_confirmed"] = max(run["max_floor_confirmed"], level)
            run["cleared_floor_lower_bound"] = max(run["cleared_floor_lower_bound"], level - 1)
        if run["floors"]:
            previous = run["floors"][-1]
            previous["seconds_until_next_floor"] = round(
                self.clock() - self.started - previous["session_seconds"], 3
            )
        run["floors"].append({"floor": level, "source": source,
                              "session_seconds": round(self.clock() - self.started, 3)})
        self.emit("floor_entered", floor=level, source=source)
        self.floor_source = None

    def end_run(self, outcome, reason=None):
        if self.run is None or self.run["outcome"] != "running":
            return
        run = self.run
        if self.battle is not None:
            self.emit("battle_finished", result="interrupted")
        run["outcome"] = outcome
        run["reason"] = _reason(reason or self.stop_reason)
        run["elapsed_seconds"] = round(
            self.clock() - self.started - run["started_session_seconds"], 3
        )
        # A failed/stopped run never gets credit for clearing its last floor.
        if outcome == "completed":
            run["cleared_floor_lower_bound"] = max(
                run["cleared_floor_lower_bound"], run["max_floor_confirmed"]
            )
        run["cleared_above_10_confirmed"] = run["cleared_floor_lower_bound"] > 10
        if run["floors"]:
            last = run["floors"][-1]
            last["seconds_until_run_end"] = round(
                self.clock() - self.started - last["session_seconds"], 3
            )
        reference = REFERENCE_REWARDS.get(run["max_floor_confirmed"])
        budget = self.config["configured_entry_budget"]
        options = self.config.get("solo_options") or {}
        if (outcome == "completed" and reference is not None and budget is not None
                and run["entry_mode"] == "new_start_expected"
                and self.config["hard"] and self.config.get("infinity")
                and not self.config.get("bonus") and not self.config.get("wishmaking")
                and options.get("adversities", "all") == "all"):
            run["reference_projection"] = {
                "floor_if_cleared": run["max_floor_confirmed"], "reward": reference,
                "configured_entry_budget": budget, "net": reference - budget,
                "measured": False,
                "assumptions": "All Adversities taken; new paid start; no rested bonus, retries or incidental Starlight. Not actual profit.",
            }
        self.emit("run_finished", outcome=outcome, reason=run["reason"])
        self.run = None

    def capture(self, label, writer):
        # Bounded evidence: at most 6 game-window screenshots per attempt, 20/session.
        if self.run is None or self.images >= 20 or len(self.run.get("screenshots", [])) >= 6:
            return
        label = re.sub(r"[^a-zA-Z0-9_-]", "_", label)[:50]
        if any(name.endswith("_" + label + ".png") for name in self.run.get("screenshots", [])):
            return
        filename = f"run{self.run['attempt']:03d}_{self.sequence:05d}_{label}.png"
        try:
            written = writer(self.folder / filename)
        except Exception as error:
            self.emit("screenshot_skipped", label=label, reason=type(error).__name__)
            return
        if written:
            self.images += 1
            self.run.setdefault("screenshots", []).append(filename)
            self.emit("screenshot_saved", label=label, file=filename)

    def finish(self, reason=None):
        if self.closed:
            return
        self.end_run("stopped", reason)
        self.report["status"] = "finished"
        self.report["finished_utc"] = _utc()
        self.report["reason"] = _reason(reason or self.stop_reason)
        self.emit("session_finished", reason=self.report["reason"])
        self.closed = True


def _call(method, *args, **kwargs):
    global _active
    with _lock:
        if _active is None:
            return
        try:
            return getattr(_active, method)(*args, **kwargs)
        except Exception as error:
            # Logging must never change a battle, mask its exception, or retry a run.
            logging.warning("Arayashu diagnostics unavailable (%s); game logic continues.", type(error).__name__)
            _active = None


def start_session(settings, teams, hard, version):
    global _active
    with _lock:
        _call("finish", "Superseded by a new session")
        _active = None
        if settings.get("hos_mode"):
            try:
                _active = RunJournal(settings, teams, hard, version)
                logging.info("Arayashu diagnostics: %s", _active.folder)
                print(f"Arayashu diagnostics: {_active.folder}")
            except Exception as error:
                logging.warning("Cannot create Arayashu diagnostics (%s).", type(error).__name__)


def event(name, **data):
    return _call("emit", name, **data)


def begin_run(team, selected, affinity):
    return _call("begin_run", team, selected, affinity)


def floor_read(level, source, **evidence):
    return _call("read_floor", level, source, **evidence)


def floor_entered(level):
    return _call("enter_floor", level)


def finish_run(outcome, reason=None):
    return _call("end_run", outcome, reason)


def capture(label, writer):
    return _call("capture", label, writer)


def finish_session(reason=None):
    return _call("finish", reason)


def observed_run(function=None, *, on_error=None):
    """Account for each attempted run, including exceptions and cancellation."""
    if function is None:
        return lambda target: observed_run(target, on_error=on_error)
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            result = function(*args, **kwargs)
        except Exception as error:
            event("run_exception", exception_type=type(error).__name__, reason=_reason(error))
            if on_error is not None:
                try:
                    on_error()
                except Exception:
                    event("screenshot_skipped", label="stop", reason="capture_callback_failed")
            finish_run("stopped" if type(error).__name__ in ("StopExecution", "SoloSetupError", "StopIteration") else "error", error)
            raise
        finish_run("completed" if result else "failed")
        return result
    return wrapped

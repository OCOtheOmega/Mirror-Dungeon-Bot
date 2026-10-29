"""Exercise real disk output, lifecycle hooks, and failure isolation without game I/O."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from source import run_diagnostics as log
from source.arayashu import SoloSetupError, solo_profile
from test_arayashu import function


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Mock(return_value=0.0)
        self.settings = {
            "hos_mode": True, "infinity": True, "bonus": False,
            "wishmaking": False, "buff": solo_profile()["buff"],
            "solo_options": {"adversities": "all", "max_failed_runs": 2},
            "webhook_url": "https://example.invalid/secret-token",
        }
        self.journal = log.RunJournal(self.settings, {0: {"sinners": [3, 9], "secret": "omit"}},
                                      True, "3.5.6", root=self.temp.name, clock=self.clock)

    def summary(self):
        return json.loads((self.journal.folder / "summary.json").read_text(encoding="utf-8"))

    def entries(self):
        return [json.loads(line) for line in (self.journal.folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]

    def begin(self):
        self.journal.begin_run(0, ["RYOSHU", "SINCLAIR"], ["POISE", "SLASH"])

    def floor(self, number, source="template"):
        self.journal.read_floor(number, source)
        self.journal.enter_floor(number)

    def test_settings_are_frozen_allowlisted_and_budget_is_not_actual_spending(self):
        self.settings["buff"][0] = 0
        report = self.summary()
        self.assertEqual(report["settings"]["configured_entry_budget"], 410)
        self.assertEqual(report["settings"]["buff"][0], 1)
        serialized = json.dumps(report)
        self.assertNotIn("secret-token", serialized)
        self.assertNotIn("omit", serialized)
        self.assertIsNone(report["actual_starlight_net"])

    def test_partial_report_survives_without_a_normal_shutdown(self):
        self.begin()
        self.floor(11)
        report = self.summary()
        self.assertEqual(report["status"], "running")
        self.assertEqual(report["runs"][0]["max_floor_confirmed"], 11)
        self.assertEqual(report["runs"][0]["cleared_floor_lower_bound"], 10)
        self.assertFalse((self.journal.folder / "summary.tmp").exists())

    def test_failure_on_floor_11_does_not_count_as_clearing_above_10(self):
        self.begin()
        self.floor(11)
        self.journal.end_run("failed")
        run = self.summary()["runs"][0]
        self.assertFalse(run["cleared_above_10_confirmed"])
        self.assertNotIn("reference_projection", run)

    def test_fallback_floor_is_never_reported_as_a_confirmed_clear(self):
        self.begin()
        self.floor(11, "fallback_increment")
        self.journal.end_run("completed")
        run = self.summary()["runs"][0]
        self.assertEqual(run["max_floor_estimate"], 11)
        self.assertEqual(run["max_floor_confirmed"], 0)
        self.assertFalse(run["cleared_above_10_confirmed"])
        self.assertNotIn("reference_projection", run)

    def test_success_projection_is_labelled_and_does_not_hide_a_failed_attempt(self):
        self.begin()
        self.floor(11)
        self.journal.end_run("failed")
        self.clock.return_value = 100.0
        self.begin()
        self.journal.emit("entry_screen", screen="Drive")
        self.floor(11)
        self.journal.end_run("completed")
        report = self.summary()
        run = report["runs"][1]
        self.assertEqual(run["reference_projection"]["net"], 10)
        self.assertFalse(run["reference_projection"]["measured"])
        self.assertTrue(run["cleared_above_10_confirmed"])
        self.assertEqual(report["failed_runs"], 1)
        self.assertEqual(report["completed_runs"], 1)
        self.assertIsNone(report["actual_starlight_net"])
        self.assertIsNone(run["actual_starlight_net"])
        events = self.entries()
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual([e["run"] for e in events if e["event"] == "run_started"], [1, 2])

    def test_resume_and_no_adversities_cannot_claim_the_reference_profit(self):
        for mode in ("resume", "none"):
            self.begin()
            self.journal.emit("entry_screen", screen="Start")
            if mode == "resume":
                self.journal.emit("entry_resume_clicked")
            else:
                self.journal.config["solo_options"]["adversities"] = "none"
            self.floor(15)
            self.journal.end_run("completed")
            self.assertNotIn("reference_projection", self.summary()["runs"][-1])

    def test_battle_duration_deaths_web_and_cost_are_available_in_summary(self):
        self.begin()
        self.journal.emit("battle_started", floor=3)
        self.clock.return_value = 12.5
        self.journal.emit("defense_submitted", floor=3, turn=1)
        self.journal.emit("battle_finished", floor=3, result="Move", retries=1)
        self.journal.emit("deployment_observed", floor=3, dead=9, alive=["RYOSHU"])
        self.journal.emit("inventory_scanned", gifts=["spiderweb"], spiderweb_recognized=True)
        cost = {"floor": 13, "displayed_cost": 900, "effective_cost": 300}
        self.journal.emit("cost_observed", **cost)
        self.journal.emit("cost_observed", **cost)
        run = self.summary()["runs"][0]
        self.assertEqual(run["battles"][0]["seconds"], 12.5)
        self.assertEqual(run["solo_confirmed_on_floor"], 3)
        self.assertTrue(run["last_spiderweb_recognized"])
        self.assertEqual(run["last_cost"], cost)
        self.assertEqual(run["event_counts"]["cost_observed"], 1)

    def test_screenshots_are_deduplicated_and_bounded(self):
        self.begin()
        writer = Mock(return_value=True)
        self.journal.capture("defense", writer)
        self.journal.capture("defense", writer)
        for index in range(20):
            self.journal.capture(f"checkpoint{index}", writer)
        self.assertEqual(writer.call_count, 6)
        self.assertEqual(len(self.summary()["runs"][0]["screenshots"]), 6)
        self.assertTrue(all(call.args[0].parent == self.journal.folder for call in writer.call_args_list))

    def test_failed_screenshot_keeps_the_structured_journal_alive(self):
        self.begin()
        self.journal.capture("stop", Mock(side_effect=OSError("disk")))
        self.journal.emit("after_screenshot_failure")
        self.assertEqual(self.entries()[-1]["event"], "after_screenshot_failure")

    def test_cancellation_and_exception_keep_the_original_control_flow(self):
        self.begin()
        error = SoloSetupError("Spiderweb not recognized")
        capture = Mock()
        @log.observed_run(on_error=capture)
        def bad_run():
            raise error
        with patch.object(log, "_active", self.journal):
            with self.assertRaises(SoloSetupError) as caught:
                bad_run()
            log.finish_session()
        self.assertIs(caught.exception, error)
        capture.assert_called_once()
        report = self.summary()
        self.assertEqual(report["runs"][0]["outcome"], "stopped")
        self.assertEqual(report["runs"][0]["reason"], "Spiderweb not recognized")
        self.assertEqual(report["status"], "finished")

    def test_logger_disk_error_does_not_mask_the_game_exception(self):
        self.begin()
        error = ValueError("original game failure")
        @log.observed_run
        def bad_run():
            raise error
        with patch.object(log, "_active", self.journal), patch.object(self.journal, "emit", side_effect=OSError("disk full")):
            with self.assertRaises(ValueError) as caught:
                bad_run()
            self.assertIsNone(log._active)
        self.assertIs(caught.exception, error)

    def test_finish_without_a_result_is_stopped_and_idempotent(self):
        self.begin()
        self.journal.emit("stop_requested", reason="Cancelled by user")
        self.journal.finish()
        count = len(self.entries())
        self.journal.finish()
        self.assertEqual(len(self.entries()), count)
        self.assertEqual(self.summary()["runs"][0]["outcome"], "stopped")
        self.assertEqual(self.summary()["reason"], "Cancelled by user")


class HookTests(unittest.TestCase):
    def test_screenshot_hook_never_captures_another_foreground_application(self):
        journal = Mock()
        grab = Mock()
        snapshot, _ = function("source/utils/utils.py", "diagnostic_snapshot", {
            "p": SimpleNamespace(HOS_MODE=True, IN_LUX=False, LIMBUS_NAME="LimbusCompany"),
            "gui": SimpleNamespace(getActiveWindowTitle=lambda: "Private document"),
            "runlog": journal, "screenshot": grab,
        })
        snapshot("stop")
        journal.capture.assert_not_called()
        grab.assert_not_called()

    def test_ordinary_mode_does_not_create_a_diagnostics_folder(self):
        with patch.object(log, "_active", None), patch.object(log, "RunJournal") as create:
            log.start_session({"hos_mode": False}, {}, True, "3.5.6")
            create.assert_not_called()


if __name__ == "__main__":
    unittest.main()

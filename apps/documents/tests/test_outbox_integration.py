import importlib.util
import json

from django.test import SimpleTestCase


class OutboxIntegrationSafetyTests(SimpleTestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("scripts.outbox_checks"), "Core delivery needs isolated fault checks")
        from scripts.outbox_checks import run_outbox_checks
        self.run_checks = run_outbox_checks

    def test_core_fault_checks_reject_a_non_disposable_compose_project(self):
        def run(arguments, **kwargs):
            if arguments[-3:] != ["config", "--format", "json"]:
                raise AssertionError("Unsafe stack must never receive mutations")
            return json.dumps({"name": "production"})
        with self.assertRaises(RuntimeError):
            self.run_checks(compose=["docker", "compose"], run=run)

    def test_phase_one_does_not_activate_unimplemented_phase_two_checks(self):
        def run(*args, **kwargs):
            raise AssertionError("Unsupported profile must not touch Docker")
        with self.assertRaises(ValueError):
            self.run_checks(compose=["docker", "compose"], phase="full", run=run)

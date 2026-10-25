from django.apps import apps
from django.core.management import get_commands
from django.test import SimpleTestCase


class OutboxAppWiringTests(SimpleTestCase):
    def test_model_ownership_matches_the_new_migration_boundary(self):
        self.assertTrue(apps.is_installed("apps.outbox"))
        self.assertEqual(
            {model.__name__ for model in apps.get_app_config("outbox").get_models()},
            {"OutboxMessage", "OutboxReceipt"},
        )
        self.assertEqual(
            {model.__name__ for model in apps.get_app_config("documents").get_models()},
            {"Document", "DocumentAuditEvent"},
        )

    def test_command_ownership_keeps_upload_recovery_in_the_domain(self):
        commands = get_commands()
        for name in ("run_outbox_relay", "outbox_status", "replay_outbox"):
            self.assertEqual(commands[name], "apps.outbox")
        for name in ("recover_outbox_uploads", "requeue_pending_documents"):
            self.assertEqual(commands[name], "apps.documents")

    def test_repeated_app_initialization_does_not_query_the_database(self):
        config = apps.get_app_config("documents")
        config.ready()
        config.ready()

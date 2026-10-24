from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.documents.outbox_operations import replay_dead_messages


class Command(BaseCommand):
    help = "Replay explicit DEAD event IDs while preserving event identity and document state."

    def add_arguments(self, parser):
        parser.add_argument("event_ids", nargs="+", type=UUID)

    def handle(self, *args, **options):
        try:
            count = replay_dead_messages(event_ids=options["event_ids"], now=timezone.now())
        except ValidationError:
            raise CommandError("Replay requires 1–100 existing DEAD event IDs.") from None
        self.stdout.write(f"Requeued {count} event(s).")

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.documents.outbox_recovery import recover_uploads


class Command(BaseCommand):
    help = "Recover stale unreceipted uploads and seed legacy PENDING rows without broker I/O."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 1000:
            raise CommandError("Recovery limit must be between 1 and 1000.")
        count = recover_uploads(now=timezone.now(), limit=options["limit"], dry_run=options["dry_run"])
        self.stdout.write(f"{'Would recover' if options['dry_run'] else 'Recovered'} {count} upload obligation(s).")

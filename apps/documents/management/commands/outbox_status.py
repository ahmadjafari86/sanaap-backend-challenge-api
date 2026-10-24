import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.documents.outbox_operations import get_outbox_status


class Command(BaseCommand):
    help = "Inspect bounded outbox counts, ages, leases and DEAD IDs without payloads."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(get_outbox_status(now=timezone.now()), sort_keys=True))

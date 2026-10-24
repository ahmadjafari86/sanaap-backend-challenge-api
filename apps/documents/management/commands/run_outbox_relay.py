import logging
from pathlib import Path
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, close_old_connections

from apps.documents.outbox_delivery import relay_once

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Deliver committed outbox obligations independently of the Celery broker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--batch-size", type=int, default=settings.OUTBOX_BATCH_SIZE)
        parser.add_argument("--poll-interval", type=float, default=settings.OUTBOX_POLL_INTERVAL_SECONDS)

    def handle(self, *args, **options):
        if not 1 <= options["batch_size"] <= 100 or not .1 <= options["poll_interval"] <= 60:
            raise CommandError("Batch must be 1–100; poll interval must be 0.1–60 seconds.")
        stopping = False
        def stop(*_):
            nonlocal stopping
            stopping = True
        previous = signal.signal(signal.SIGTERM, stop)
        try:
            while not stopping:
                try:
                    count = relay_once(batch_size=options["batch_size"])
                    Path(settings.OUTBOX_RELAY_HEARTBEAT_FILE).write_text(str(time.time()))
                except DatabaseError:
                    close_old_connections()
                    if options["once"]:
                        raise CommandError("Outbox database temporarily unavailable.") from None
                    logger.warning("Outbox database temporarily unavailable.")
                    time.sleep(max(1, options["poll_interval"]))
                    continue
                if options["once"]:
                    self.stdout.write(f"Attempted {count} event(s).")
                    return
                time.sleep(options["poll_interval"])
        finally:
            signal.signal(signal.SIGTERM, previous)

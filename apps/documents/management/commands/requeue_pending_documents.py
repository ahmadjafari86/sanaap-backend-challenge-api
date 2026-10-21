from django.core.management.base import BaseCommand

from apps.documents.background import publish_document
from apps.documents.models import Document


class Command(BaseCommand):
    help = "Republish PENDING document ids after broker/worker recovery; terminal rows are never retried."

    def handle(self, *args, **options):
        queued = failed = 0
        for document_id in Document.objects.filter(status=Document.Status.PENDING).values_list("pk", flat=True).iterator():
            if publish_document(document_id):
                queued += 1
            else:
                failed += 1
        self.stdout.write(f"Published {queued} pending document(s); {failed} publication failure(s).")

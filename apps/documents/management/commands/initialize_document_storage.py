from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError
from django_minio_backend.models import MinioBackend


class Command(BaseCommand):
    help = "Initialize the configured private document bucket, including MinioBackend subclasses."

    def handle(self, *args, **options):
        storage = storages["default"]
        if not isinstance(storage, MinioBackend) or storage.is_bucket_public:
            raise CommandError("Document storage must be a private MinIO backend.")
        storage.check_bucket_existence()
        self.stdout.write("Private document storage initialized.")

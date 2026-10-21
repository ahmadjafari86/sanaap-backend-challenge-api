"""Keep runtime HTTP clients out of declarative, deepcopyable Django settings."""

from django_minio_backend.models import MinioBackend
import urllib3


class DocumentStorage(MinioBackend):
    def __init__(self, **kwargs):
        kwargs.setdefault("MINIO_HTTP_CLIENT", urllib3.PoolManager(timeout=urllib3.Timeout(connect=2, read=5), retries=False))
        super().__init__(**kwargs)

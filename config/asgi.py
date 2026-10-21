"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

django_application = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import OriginValidator
from django.conf import settings
from apps.documents.routing import websocket_urlpatterns

def build_application():
    return ProtocolTypeRouter({
        "http": django_application,
        "websocket": OriginValidator(URLRouter(websocket_urlpatterns), settings.WEBSOCKET_ALLOWED_ORIGINS),
    })


application = build_application()

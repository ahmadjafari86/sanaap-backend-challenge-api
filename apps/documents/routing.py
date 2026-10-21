from django.urls import path

from .consumers import DocumentNotificationsConsumer

websocket_urlpatterns = [path("ws/documents/", DocumentNotificationsConsumer.as_asgi())]

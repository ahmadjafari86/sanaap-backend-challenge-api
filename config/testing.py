from django.test import override_settings
from django.test.runner import DiscoverRunner


class IsolatedChannelTestRunner(DiscoverRunner):
    """Fixture document IDs must never reach running application's subscribers."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self.channel_settings = override_settings(CHANNEL_LAYERS={
            "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
        })
        self.channel_settings.enable()

    def teardown_test_environment(self, **kwargs):
        try:
            self.channel_settings.disable()
        finally:
            super().teardown_test_environment(**kwargs)

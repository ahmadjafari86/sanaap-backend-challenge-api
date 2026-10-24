from copy import deepcopy

from django.test import SimpleTestCase

from scripts.integration_check import is_isolated_config


class IntegrationIsolationTests(SimpleTestCase):
    def setUp(self):
        self.project = "sanaap-t09-a123456789"
        self.config = {
            "name": self.project,
            "volumes": {
                "postgres_data": {"name": f"{self.project}_postgres_data"},
                "minio_data": {"name": f"{self.project}_minio_data"},
                "static_data": {"name": f"{self.project}_static_data"},
                "staging_data": {"name": f"{self.project}_staging_data"},
                "redis_data": {"name": f"{self.project}_redis_data"},
            },
            "networks": {"default": {"name": f"{self.project}_default"}},
            "services": {
                "backend": {"volumes": [{"type": "volume", "source": "static_data", "target": "/app/staticfiles"}, {"type": "volume", "source": "staging_data", "target": "/app/.staging"}]},
                "worker": {"volumes": [{"type": "volume", "source": "staging_data", "target": "/app/.staging"}]},
                "outbox-relay": {"volumes": [{"type": "volume", "source": "staging_data", "target": "/app/.staging"}]},
                "nginx": {
                    "ports": [{"host_ip": "127.0.0.1", "target": 8080, "published": "28000"}],
                    "volumes": [{"type": "volume", "source": "static_data", "target": "/srv/static", "read_only": True}],
                },
                "postgres": {"volumes": [{"type": "volume", "source": "postgres_data", "target": "/var/lib/postgresql/data"}]},
                "redis": {},
                "minio": {"volumes": [{"type": "volume", "source": "minio_data", "target": "/data"}]},
            },
        }

    def test_allows_a_fresh_loopback_only_project_with_its_own_volumes(self):
        self.assertTrue(is_isolated_config(self.config, self.project))

    def test_rejects_the_current_project_as_a_cleanup_target(self):
        self.config["name"] = "sanaap-backend-challenge-api"
        self.assertFalse(is_isolated_config(self.config, "sanaap-backend-challenge-api"))

    def test_rejects_reusing_an_existing_database_volume(self):
        self.config["volumes"]["postgres_data"]["name"] = "sanaap-backend-challenge-api_postgres_data"
        self.assertFalse(is_isolated_config(self.config, self.project))

    def test_rejects_external_resources_and_host_directory_mounts(self):
        external = deepcopy(self.config)
        external["volumes"]["minio_data"]["external"] = True
        self.assertFalse(is_isolated_config(external, self.project))
        bind = deepcopy(self.config)
        bind["services"]["postgres"]["volumes"] = [{"type": "bind", "source": "/existing-data", "target": "/data"}]
        self.assertFalse(is_isolated_config(bind, self.project))

    def test_rejects_publishing_test_services_to_other_interfaces(self):
        for host in ("0.0.0.0", "::", None):
            with self.subTest(host=host):
                self.config["services"]["nginx"]["ports"][0]["host_ip"] = host
                self.assertFalse(is_isolated_config(self.config, self.project))

    def test_rejects_bypassing_the_proxy_with_a_published_backend_port(self):
        self.config["services"]["backend"]["ports"] = [{"host_ip": "127.0.0.1", "target": 8000, "published": "28001"}]
        self.assertFalse(is_isolated_config(self.config, self.project))

    def test_rejects_writable_proxy_access_to_static_assets(self):
        self.config["services"]["nginx"]["volumes"][0]["read_only"] = False
        self.assertFalse(is_isolated_config(self.config, self.project))

    def test_rejects_a_shared_network_or_fixed_container_name(self):
        network = deepcopy(self.config)
        network["networks"]["default"]["name"] = "existing_default"
        self.assertFalse(is_isolated_config(network, self.project))
        self.config["services"]["backend"]["container_name"] = "existing-backend"
        self.assertFalse(is_isolated_config(self.config, self.project))

    def test_rejects_worker_without_shared_staging(self):
        self.config["services"]["worker"]["volumes"] = []
        self.assertFalse(is_isolated_config(self.config, self.project))

    def test_rejects_publishing_worker_ports(self):
        self.config["services"]["worker"]["ports"] = [{"host_ip": "127.0.0.1", "target": 8000}]
        self.assertFalse(is_isolated_config(self.config, self.project))

    def test_rejects_relay_without_staging_or_with_published_ports(self):
        no_staging = deepcopy(self.config)
        no_staging["services"]["outbox-relay"]["volumes"] = []
        self.assertFalse(is_isolated_config(no_staging, self.project))
        self.config["services"]["outbox-relay"]["ports"] = [{"host_ip": "127.0.0.1", "target": 8000}]
        self.assertFalse(is_isolated_config(self.config, self.project))

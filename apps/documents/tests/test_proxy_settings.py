import json
import os
import subprocess
import sys

from django.test import SimpleTestCase


PROXY_PROBE = """
import json
import django
django.setup()
from django.conf import settings
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.middleware.security import SecurityMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

settings.SESSION_ENGINE = 'django.contrib.sessions.backends.signed_cookies'
def respond(request):
    request.session['probe'] = True
    get_token(request)
    return HttpResponse('ok')

import os
request = RequestFactory().get('/', HTTP_HOST='localhost', HTTP_X_FORWARDED_PROTO=os.environ['PROBE_SCHEME'])
response = SecurityMiddleware(SessionMiddleware(CsrfViewMiddleware(respond)))(request)
print(json.dumps({
    'secure': request.is_secure(), 'status': response.status_code,
    'location': response.get('Location'),
    'session_secure': bool(response.cookies.get('sessionid', {}).get('secure')),
    'csrf_secure': bool(response.cookies.get('csrftoken', {}).get('secure')),
}))
"""


class ProxySettingsTests(SimpleTestCase):
    def probe(self, *, trusted=False, scheme="https", redirect=False, secure_cookies=False):
        proxy_env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "DJANGO_TRUST_PROXY": str(trusted),
            "DJANGO_SECURE_SSL_REDIRECT": str(redirect),
            "DJANGO_SECURE_COOKIES": str(secure_cookies),
            "DJANGO_ALLOWED_HOSTS": "localhost",
            "PROBE_SCHEME": scheme,
        }
        output = subprocess.check_output([sys.executable, "-B", "-c", PROXY_PROBE], env=proxy_env, timeout=15)
        return json.loads(output)

    def test_native_mode_does_not_trust_client_supplied_https(self):
        result = self.probe(trusted=False)
        self.assertFalse(result["secure"])
        self.assertEqual(result["status"], 200)

    def test_trusted_proxy_recognizes_https(self):
        result = self.probe(trusted=True)
        self.assertTrue(result["secure"])
        self.assertEqual(result["status"], 200)

    def test_production_redirect_uses_original_host(self):
        result = self.probe(trusted=True, scheme="http", redirect=True)
        self.assertEqual(result["status"], 301)
        self.assertEqual(result["location"], "https://localhost/")

    def test_trusted_https_does_not_enter_redirect_loop(self):
        result = self.probe(trusted=True, redirect=True)
        self.assertTrue(result["secure"])
        self.assertEqual(result["status"], 200)
        self.assertIsNone(result["location"])

    def test_production_cookies_have_secure_attribute(self):
        result = self.probe(trusted=True, secure_cookies=True)
        self.assertTrue(result["session_secure"])
        self.assertTrue(result["csrf_secure"])

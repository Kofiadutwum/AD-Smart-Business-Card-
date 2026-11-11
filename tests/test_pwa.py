"""The installable app: manifest, service worker, offline page and the install button."""

from django.contrib.staticfiles import finders

from apps.cards.services import RESERVED

from .helpers import BaseTest


class InstallableAppTests(BaseTest):
    def test_manifest_describes_a_standalone_app_with_every_icon(self):
        response = self.client.get("/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        data = response.json()
        for key in ("name", "short_name", "start_url", "scope", "id", "theme_color", "background_color"):
            self.assertTrue(data[key], key)
        self.assertEqual(data["display"], "standalone")
        self.assertTrue(data["start_url"].startswith(data["scope"]))
        sizes = {(icon["sizes"], icon["purpose"]) for icon in data["icons"]}
        self.assertIn(("192x192", "any"), sizes)
        self.assertIn(("512x512", "any"), sizes)
        self.assertIn(("512x512", "maskable"), sizes)

    def test_icon_files_exist(self):
        for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png", "logo-96.webp"):
            self.assertTrue(finders.find(f"brand/{name}"), name)

    def test_service_worker_is_served_from_the_root_and_never_cached(self):
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response["Content-Type"])
        self.assertEqual(response["Cache-Control"], "no-cache")
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        body = response.content.decode()
        self.assertIn('"/offline"', body)  # the page kept for when there is no connection
        self.assertIn("logo-96", body)
        self.assertIn('var CACHE = "adsmart-', body)

    def test_service_worker_never_stores_pages_or_posts(self):
        body = self.client.get("/sw.js").content.decode()
        self.assertIn('request.method !== "GET"', body)
        # Pages are fetched, never put in the cache: the only cache.put is for /static/ files.
        self.assertEqual(body.count("cache.put"), 1)
        self.assertIn('url.pathname.indexOf("/static/") === 0', body)

    def test_offline_page_shows_without_signing_in_and_is_kept_out_of_search(self):
        response = self.client.get("/offline")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You are offline")
        self.assertContains(response, 'name="robots" content="noindex')
        self.assertContains(response, "location.reload()")

    def test_pages_link_the_manifest_and_carry_the_install_item(self):
        response = self.client.get("/")
        self.assertContains(response, '<link rel="manifest" href="/manifest.webmanifest">')
        self.assertContains(response, 'name="apple-mobile-web-app-capable"')
        self.assertContains(response, "data-install-item hidden")  # shown by site.js only where the browser can install

    def test_home_screen_icon_is_served_at_the_root_paths_an_iphone_asks_for(self):
        for path in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response["Content-Type"], "image/png", path)
            self.assertEqual(response.getvalue()[:8], b"\x89PNG\r\n\x1a\n", path)
        self.assertContains(self.client.get("/"), 'rel="apple-touch-icon" sizes="180x180"')

    def test_install_text_is_translated(self):
        response = self.client.get("/", HTTP_ACCEPT_LANGUAGE="fr")
        self.assertContains(response, "Installer l")

    def test_offline_and_shops_cannot_be_taken_as_a_card_address(self):
        self.assertIn("offline", RESERVED)
        self.assertIn("shops", RESERVED)

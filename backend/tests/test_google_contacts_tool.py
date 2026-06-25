import json
import unittest
from unittest.mock import patch

from tools.google_contacts_tool import CONNECTIONS_URL, SEARCH_URL, GoogleContactsTool


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class GoogleContactsToolTests(unittest.TestCase):
    def test_find_contact_warms_cache_before_searching(self) -> None:
        calls = []
        search_person = {
            "resourceName": "people/search-hit",
            "names": [{"displayName": "Jordan Rivera"}],
            "emailAddresses": [{"value": "jordan@example.com"}],
        }

        def fake_get(url, headers, params, timeout):
            calls.append((url, dict(params)))
            self.assertEqual("Bearer token", headers["Authorization"])
            if url == SEARCH_URL and params["query"] == "":
                return FakeResponse({"results": []})
            if url == SEARCH_URL and params["query"] == "Jordan":
                return FakeResponse({"results": [{"person": search_person}]})
            self.fail(f"Unexpected request: {url} {params}")

        tool = GoogleContactsTool({"access_token": "token"})
        with patch("tools.google_contacts_tool.requests.get", side_effect=fake_get):
            result = json.loads(tool.execute("google_contacts_find_contact", {"name": "Jordan"}))

        self.assertEqual(["", "Jordan"], [params["query"] for url, params in calls if url == SEARCH_URL])
        self.assertEqual("Jordan Rivera", result[0]["name"])
        self.assertEqual(["jordan@example.com"], result[0]["emails"])

    def test_find_contact_falls_back_to_local_spacing_insensitive_match(self) -> None:
        calls = []
        jordan = {
            "resourceName": "people/c1",
            "names": [{"displayName": "Jordan Rivera"}],
            "emailAddresses": [{"value": "jordan@example.com"}],
            "organizations": [{"name": "Example Org"}],
        }
        someone_else = {
            "resourceName": "people/other",
            "names": [{"displayName": "Kelly Stone"}],
            "emailAddresses": [{"value": "kelly@example.com"}],
        }

        def fake_get(url, headers, params, timeout):
            calls.append((url, dict(params)))
            if url == SEARCH_URL:
                return FakeResponse({"results": []})
            if url == CONNECTIONS_URL:
                return FakeResponse({"connections": [someone_else, jordan]})
            self.fail(f"Unexpected request: {url} {params}")

        tool = GoogleContactsTool({"access_token": "token"})
        with patch("tools.google_contacts_tool.requests.get", side_effect=fake_get):
            result = json.loads(tool.execute("google_contacts_find_contact", {"name": "j o r d a n"}))

        self.assertEqual(["", "j o r d a n"], [params["query"] for url, params in calls if url == SEARCH_URL])
        self.assertTrue(any(url == CONNECTIONS_URL for url, _ in calls))
        self.assertEqual(1, len(result))
        self.assertEqual("Jordan Rivera", result[0]["name"])
        self.assertEqual(["jordan@example.com"], result[0]["emails"])
        self.assertEqual(["Example Org"], result[0]["organizations"])

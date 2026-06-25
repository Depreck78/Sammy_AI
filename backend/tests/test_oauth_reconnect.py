import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app.main import auth_failure_requires_reconnect, migrate_zoho_mail_credentials, oauth_start
from tools.zoho_mail_tool import ZohoMailTool


class FakeResponse:
    ok = False
    status_code = 400
    text = '{"error":"invalid_code"}'


class OAuthReconnectTests(unittest.IsolatedAsyncioTestCase):
    def test_external_zoho_config_does_not_override_managed_credentials(self) -> None:
        existing = {"client_id": "managed-client"}
        with (
            patch("app.main.db.get_tool_credentials", return_value=existing),
            patch("app.main.db.save_tool_credentials") as save_credentials,
            patch("tools.zoho_mail_tool.ZohoMailTool.load_external_credentials") as load_external,
        ):
            migrate_zoho_mail_credentials()

        load_external.assert_not_called()
        save_credentials.assert_not_called()

    def test_zoho_revoked_refresh_token_requires_reconnect(self) -> None:
        tool = ZohoMailTool(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "revoked-token",
                "account_id": "account-id",
                "from_address": "sender@example.com",
            }
        )

        with patch("tools.zoho_mail_tool.requests.post", return_value=FakeResponse()):
            result = tool.execute("zoho_mail_list_accounts", {})

        self.assertIn("invalid_code", result)
        self.assertTrue(auth_failure_requires_reconnect(result))

    async def test_zoho_reconnect_forces_new_consent(self) -> None:
        credentials = {"client_id": "client-id", "client_secret": "client-secret", "dc": "com"}
        with (
            patch("app.main.db.get_tool_credentials", return_value=credentials),
            patch("app.main.db.save_oauth_state"),
        ):
            response = await oauth_start("zoho_mail")

        query = parse_qs(urlparse(response.headers["location"]).query)
        self.assertEqual(["offline"], query["access_type"])
        self.assertEqual(["consent"], query["prompt"])


if __name__ == "__main__":
    unittest.main()

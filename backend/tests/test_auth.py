import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app.main import app, tts_voices


class FakeElevenLabsResponse:
    status_code = 401
    text = "Unauthorized"


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, *args, **kwargs):
        return FakeElevenLabsResponse()


class AuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_401_is_explicitly_identified(self) -> None:
        transport = httpx.ASGITransport(app=app)
        with (
            patch("app.main.access_password_enabled", return_value=True),
            patch("app.main.authenticated", return_value=False),
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/settings")

        self.assertEqual(401, response.status_code)
        self.assertEqual("true", response.headers.get("X-Sammy-Auth-Required"))

    async def test_elevenlabs_401_is_an_upstream_error(self) -> None:
        with (
            patch("app.main.elevenlabs_api_key", return_value="invalid-key"),
            patch("app.main.httpx.AsyncClient", FakeAsyncClient),
        ):
            with self.assertRaises(HTTPException) as raised:
                await tts_voices()

        self.assertEqual(502, raised.exception.status_code)
        self.assertIn("ElevenLabs rejected", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()

import base64
from email import message_from_bytes
from email.policy import default
import unittest
from unittest.mock import patch

from tools.gmail_tool import GmailTool


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def decode_raw_message(raw):
    padding = "=" * (-len(raw) % 4)
    return message_from_bytes(base64.urlsafe_b64decode(raw + padding), policy=default)


def normalize_newlines(value):
    return value.replace("\r\n", "\n")


class GmailToolTests(unittest.TestCase):
    def test_send_email_treats_br_body_as_html_with_plain_text_fallback(self) -> None:
        tool = GmailTool({"access_token": "token"})
        body = "Dear Rachael,<br><br>Thank you for your help.<br><br>Warm regards,<br>Erick"

        with patch("tools.gmail_tool.requests.post", return_value=FakeResponse({"id": "sent-1"})) as post:
            result = tool.execute(
                "gmail_send_email",
                {
                    "to": "Rachael <rachael@example.com>",
                    "subject": "Thanks",
                    "body": body,
                },
            )

        self.assertNotIn("Gmail tool error", result)
        payload = post.call_args.kwargs["json"]
        self.assertNotIn("threadId", payload)
        message = decode_raw_message(payload["raw"])
        self.assertEqual("Rachael <rachael@example.com>", message["To"])
        self.assertEqual("Thanks", message["Subject"])
        self.assertTrue(message.is_multipart())

        parts = {
            part.get_content_type(): part.get_content()
            for part in message.walk()
            if part.get_content_maintype() != "multipart"
        }
        self.assertIn("Dear Rachael,\n\nThank you for your help.", normalize_newlines(parts["text/plain"]))
        self.assertIn("<br><br>", parts["text/html"])

    def test_reply_email_preserves_gmail_thread_and_reply_headers(self) -> None:
        tool = GmailTool({"access_token": "token"})
        original = {
            "id": "msg-1",
            "threadId": "thread-1",
            "payload": {
                "headers": [
                    {"name": "From", "value": "Rachael <rachael@example.com>"},
                    {"name": "Reply-To", "value": "Visas Boston <visas@example.com>"},
                    {"name": "Subject", "value": "SEVIS history"},
                    {"name": "Message-ID", "value": "<original@example.com>"},
                    {"name": "References", "value": "<older@example.com>"},
                ]
            },
        }

        with (
            patch("tools.gmail_tool.requests.get", return_value=FakeResponse(original)) as get,
            patch("tools.gmail_tool.requests.post", return_value=FakeResponse({"id": "reply-1"})) as post,
        ):
            result = tool.execute(
                "gmail_reply_email",
                {
                    "email_id": "msg-1",
                    "body": "Thank you for the help.",
                },
            )

        self.assertNotIn("Gmail tool error", result)
        self.assertEqual("metadata", get.call_args.kwargs["params"]["format"])

        payload = post.call_args.kwargs["json"]
        self.assertEqual("thread-1", payload["threadId"])
        message = decode_raw_message(payload["raw"])
        self.assertEqual("Visas Boston <visas@example.com>", message["To"])
        self.assertEqual("Re: SEVIS history", message["Subject"])
        self.assertEqual("<original@example.com>", message["In-Reply-To"])
        self.assertEqual("<older@example.com> <original@example.com>", message["References"])
        self.assertEqual("Thank you for the help.\n", normalize_newlines(message.get_content()))


if __name__ == "__main__":
    unittest.main()

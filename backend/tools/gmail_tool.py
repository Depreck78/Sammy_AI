import base64
from email.message import EmailMessage
from email.policy import SMTP
from html import unescape
import json
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from app.tooling import BaseTool


HTML_TAG_RE = re.compile(
    r"</?(?:a|b|blockquote|br|code|div|em|h[1-6]|i|li|ol|p|pre|span|strong|table|tbody|td|th|thead|tr|u|ul)(?:\s[^>]*)?/?>",
    re.IGNORECASE,
)


class GmailTool(BaseTool):
    name = "gmail"
    display_name = "Gmail"
    description = "List, search, read, send, and reply to Gmail messages."
    icon = "Mail"
    requires_auth = True

    def get_auth_fields(self) -> List[Dict[str, Any]]:
        return [
            {"name": "client_id", "label": "OAuth Client ID", "type": "text"},
            {"name": "client_secret", "label": "OAuth Client Secret", "type": "password"},
            {"name": "access_token", "label": "Access Token", "type": "password"},
            {"name": "refresh_token", "label": "Refresh Token", "type": "password"},
        ]

    def validate_auth(self, credentials: Dict[str, Any]) -> bool:
        return bool(credentials.get("access_token") or credentials.get("refresh_token"))

    def get_functions(self) -> List[Dict[str, Any]]:
        return [
            self.function(
                "gmail_list_emails",
                "List Gmail messages with sender, subject, snippet, and id.",
                {
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
                    "query": {"type": "string"},
                },
            ),
            self.function(
                "gmail_get_email",
                "Get a Gmail message body and metadata by id.",
                {"email_id": {"type": "string"}},
                ["email_id"],
            ),
            self.function(
                "gmail_send_email",
                "Send a new email with Gmail. Use gmail_reply_email when answering an existing email so the reply stays in its thread.",
                {
                    "to": {"type": "string"},
                    "cc": {"type": "string", "description": "Optional comma-separated Cc recipients."},
                    "bcc": {"type": "string", "description": "Optional comma-separated Bcc recipients."},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "body_format": {
                        "type": "string",
                        "enum": ["auto", "plain", "html"],
                        "description": "Defaults to auto. Auto sends HTML bodies, including <br> line breaks, as HTML with a plain-text fallback.",
                    },
                },
                ["to", "subject", "body"],
            ),
            self.function(
                "gmail_reply_email",
                "Reply to an existing Gmail message by id, preserving the Gmail thread and reply headers.",
                {
                    "email_id": {"type": "string", "description": "Gmail message id to reply to."},
                    "body": {"type": "string"},
                    "to": {"type": "string", "description": "Optional recipient override. Defaults to Reply-To or From on the original message."},
                    "cc": {"type": "string", "description": "Optional comma-separated Cc recipients."},
                    "bcc": {"type": "string", "description": "Optional comma-separated Bcc recipients."},
                    "subject": {"type": "string", "description": "Optional subject override. Defaults to Re: original subject."},
                    "body_format": {
                        "type": "string",
                        "enum": ["auto", "plain", "html"],
                        "description": "Defaults to auto. Auto sends HTML bodies, including <br> line breaks, as HTML with a plain-text fallback.",
                    },
                },
                ["email_id", "body"],
            ),
            self.function(
                "gmail_search_emails",
                "Search Gmail messages.",
                {"query": {"type": "string"}},
                ["query"],
            ),
        ]

    def _headers(self) -> Dict[str, str]:
        token = self.credentials.get("access_token")
        if not token:
            raise RuntimeError("Gmail is not connected. Add OAuth tokens in Tools settings.")
        return {"Authorization": f"Bearer {token}"}

    def _message_headers(self, payload: Dict[str, Any]) -> Dict[str, str]:
        headers = {}
        for item in payload.get("headers", []):
            headers[item.get("name", "").lower()] = item.get("value", "")
        return headers

    def _decode_body_data(self, data: str) -> str:
        padding = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")

    def _body_from_payload(self, payload: Dict[str, Any]) -> str:
        data = payload.get("body", {}).get("data")
        if data:
            return self._decode_body_data(data)
        for part in payload.get("parts", []) or []:
            if part.get("mimeType", "").startswith("text/plain"):
                return self._body_from_payload(part)
        for part in payload.get("parts", []) or []:
            if part.get("mimeType", "").startswith("text/html"):
                return self._body_from_payload(part)
        return ""

    def _get_message(self, email_id: str, message_format: str = "full") -> Dict[str, Any]:
        response = requests.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{email_id}",
            headers=self._headers(),
            params={"format": message_format},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def _list(self, max_results: int, query: str) -> str:
        response = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers=self._headers(),
            params={"maxResults": max_results, "q": query or ""},
            timeout=15,
        )
        response.raise_for_status()
        messages = response.json().get("messages", [])
        out = []
        for item in messages:
            data = self._get_message(item["id"], "metadata")
            headers = self._message_headers(data.get("payload", {}))
            out.append(
                {
                    "id": item["id"],
                    "threadId": data.get("threadId", ""),
                    "from": headers.get("from", ""),
                    "subject": headers.get("subject", ""),
                    "date": headers.get("date", ""),
                    "snippet": data.get("snippet", ""),
                }
            )
        return json.dumps(out, indent=2)

    def _looks_like_html(self, body: str) -> bool:
        return bool(HTML_TAG_RE.search(body or ""))

    def _plain_text_from_html(self, body: str) -> str:
        text = re.sub(r"<\s*br\s*/?\s*>", "\n", body, flags=re.IGNORECASE)
        text = re.sub(r"</\s*(?:p|div|h[1-6]|blockquote|tr)\s*>", "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<\s*li(?:\s[^>]*)?>", "- ", text, flags=re.IGNORECASE)
        text = re.sub(r"</\s*li\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = unescape(text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _body_variants(self, body: str, body_format: str = "auto") -> Tuple[str, Optional[str]]:
        body = body or ""
        body_format = (body_format or "auto").lower()
        if body_format not in {"auto", "plain", "html"}:
            body_format = "auto"
        if body_format == "html" or (body_format == "auto" and self._looks_like_html(body)):
            return self._plain_text_from_html(body) or body, body
        if body_format == "plain" and self._looks_like_html(body):
            return self._plain_text_from_html(body), None
        return body, None

    def _build_message(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        body_format: str = "auto",
        cc: str = "",
        bcc: str = "",
        headers: Optional[Dict[str, str]] = None,
    ) -> EmailMessage:
        plain_body, html_body = self._body_variants(body, body_format)
        message = EmailMessage()
        message["To"] = to
        if cc:
            message["Cc"] = cc
        if bcc:
            message["Bcc"] = bcc
        message["Subject"] = subject
        for name, value in (headers or {}).items():
            if value:
                message[name] = value
        message.set_content(plain_body)
        if html_body is not None:
            message.add_alternative(html_body, subtype="html")
        return message

    def _send_message(self, message: EmailMessage, thread_id: str = "") -> Dict[str, Any]:
        encoded = base64.urlsafe_b64encode(message.as_bytes(policy=SMTP)).decode("utf-8")
        payload = {"raw": encoded}
        if thread_id:
            payload["threadId"] = thread_id
        response = requests.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers=self._headers(),
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def _reply_subject(self, subject: str) -> str:
        subject = (subject or "").strip()
        if not subject or re.match(r"^\s*re\s*:", subject, flags=re.IGNORECASE):
            return subject
        return f"Re: {subject}"

    def _reply_references(self, headers: Dict[str, str]) -> str:
        references = (headers.get("references") or "").strip()
        message_id = (headers.get("message-id") or "").strip()
        if not message_id:
            return references
        if message_id in references:
            return references
        return f"{references} {message_id}".strip()

    def execute(self, function_name: str, parameters: Dict[str, Any]) -> str:
        try:
            parameters = parameters or {}
            if function_name in {"gmail_list_emails", "gmail_search_emails"}:
                return self._list(
                    int(parameters.get("max_results") or 10),
                    parameters.get("query") or "",
                )
            if function_name == "gmail_get_email":
                email_id = parameters["email_id"]
                data = self._get_message(email_id, "full")
                headers = self._message_headers(data.get("payload", {}))
                body = self._body_from_payload(data.get("payload", {}))
                return json.dumps(
                    {
                        "id": data.get("id"),
                        "threadId": data.get("threadId"),
                        "from": headers.get("from", ""),
                        "to": headers.get("to", ""),
                        "replyTo": headers.get("reply-to", ""),
                        "subject": headers.get("subject", ""),
                        "date": headers.get("date", ""),
                        "messageId": headers.get("message-id", ""),
                        "references": headers.get("references", ""),
                        "snippet": data.get("snippet", ""),
                        "body": body,
                    },
                    indent=2,
                )
            if function_name == "gmail_send_email":
                message = self._build_message(
                    to=parameters["to"],
                    cc=parameters.get("cc") or "",
                    bcc=parameters.get("bcc") or "",
                    subject=parameters["subject"],
                    body=parameters["body"],
                    body_format=parameters.get("body_format") or "auto",
                )
                return json.dumps(self._send_message(message), indent=2)
            if function_name == "gmail_reply_email":
                original = self._get_message(parameters["email_id"], "metadata")
                headers = self._message_headers(original.get("payload", {}))
                message_id = (headers.get("message-id") or "").strip()
                to = parameters.get("to") or headers.get("reply-to") or headers.get("from")
                if not to:
                    raise RuntimeError("Original message has no Reply-To or From header. Provide a 'to' recipient.")
                message = self._build_message(
                    to=to,
                    cc=parameters.get("cc") or "",
                    bcc=parameters.get("bcc") or "",
                    subject=self._reply_subject(parameters.get("subject") or headers.get("subject", "")),
                    body=parameters["body"],
                    body_format=parameters.get("body_format") or "auto",
                    headers={
                        "In-Reply-To": message_id,
                        "References": self._reply_references(headers),
                    },
                )
                return json.dumps(self._send_message(message, original.get("threadId") or ""), indent=2)
        except Exception as exc:
            return f"Gmail tool error: {exc}"
        return f"Unknown Gmail function: {function_name}"

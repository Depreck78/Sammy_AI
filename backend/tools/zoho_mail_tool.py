import json
import os
from pathlib import Path
from typing import Any, Dict, List

import requests

from app.tooling import BaseTool


DC_HOSTS = {
    "com": {"accounts": "https://accounts.zoho.com", "mail": "https://mail.zoho.com"},
    "eu": {"accounts": "https://accounts.zoho.eu", "mail": "https://mail.zoho.eu"},
    "in": {"accounts": "https://accounts.zoho.in", "mail": "https://mail.zoho.in"},
    "com.au": {"accounts": "https://accounts.zoho.com.au", "mail": "https://mail.zoho.com.au"},
    "jp": {"accounts": "https://accounts.zoho.jp", "mail": "https://mail.zoho.jp"},
    "ca": {"accounts": "https://accounts.zohocloud.ca", "mail": "https://mail.zoho.ca"},
    "sa": {"accounts": "https://accounts.zoho.sa", "mail": "https://mail.zoho.sa"},
}


def _config_path() -> Path:
    return Path(os.environ.get("ZOHO_MAIL_CONFIG", "~/.config/zoho-mail-plugin/config.json")).expanduser()


def _read_config_file() -> Dict[str, Any]:
    target = _config_path()
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Unable to read Zoho Mail config file {target}: {exc}") from exc


def _pick(config: Dict[str, Any], env_name: str, *keys: str) -> str:
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value
    for key in keys:
        value = config.get(key)
        if value:
            return str(value)
    return ""


class ZohoMailTool(BaseTool):
    name = "zoho_mail"
    display_name = "Zoho Mail"
    description = "List, search, read, draft, reply, and send Zoho Mail messages."
    icon = "Mail"
    requires_auth = True

    @staticmethod
    def load_external_credentials() -> Dict[str, Any]:
        config = _read_config_file()
        dc = _pick(config, "ZOHO_MAIL_DC", "dc") or "com"
        hosts = DC_HOSTS.get(dc, DC_HOSTS["com"])
        credentials = {
            "dc": dc,
            "client_id": _pick(config, "ZOHO_MAIL_CLIENT_ID", "client_id", "clientId"),
            "client_secret": _pick(config, "ZOHO_MAIL_CLIENT_SECRET", "client_secret", "clientSecret"),
            "refresh_token": _pick(config, "ZOHO_MAIL_REFRESH_TOKEN", "refresh_token", "refreshToken"),
            "account_id": _pick(config, "ZOHO_MAIL_ACCOUNT_ID", "account_id", "accountId"),
            "from_address": _pick(config, "ZOHO_MAIL_FROM_ADDRESS", "from_address", "fromAddress"),
            "accounts_base": _pick(config, "ZOHO_MAIL_ACCOUNTS_BASE", "accounts_base", "accountsBase") or hosts["accounts"],
            "api_base": _pick(config, "ZOHO_MAIL_API_BASE", "api_base", "apiBase") or hosts["mail"],
        }
        return {key: value for key, value in credentials.items() if value}

    def get_auth_fields(self) -> List[Dict[str, Any]]:
        return [
            {"name": "dc", "label": "Data center", "type": "select", "options": list(DC_HOSTS.keys())},
            {"name": "client_id", "label": "OAuth Client ID", "type": "text"},
            {"name": "client_secret", "label": "OAuth Client Secret", "type": "password"},
            {"name": "refresh_token", "label": "Refresh Token", "type": "password"},
            {"name": "account_id", "label": "Zoho Mail Account ID", "type": "text"},
            {"name": "from_address", "label": "From Address", "type": "text"},
            {"name": "accounts_base", "label": "Accounts API Base", "type": "text"},
            {"name": "api_base", "label": "Mail API Base", "type": "text"},
        ]

    def validate_auth(self, credentials: Dict[str, Any]) -> bool:
        return bool(
            credentials.get("client_id")
            and credentials.get("client_secret")
            and credentials.get("refresh_token")
            and credentials.get("account_id")
            and credentials.get("from_address")
        )

    def get_functions(self) -> List[Dict[str, Any]]:
        email_fields = {
            "fromAddress": {"type": "string", "description": "Sender address. Defaults to the configured from address."},
            "toAddress": {"type": "string", "description": "Recipient email address. Use comma-separated addresses for multiple recipients."},
            "ccAddress": {"type": "string", "description": "Optional comma-separated Cc addresses."},
            "bccAddress": {"type": "string", "description": "Optional comma-separated Bcc addresses."},
            "subject": {"type": "string", "description": "Email subject."},
            "content": {"type": "string", "description": "Email body. HTML is accepted by Zoho Mail."},
            "mailFormat": {"type": "string", "enum": ["html", "plaintext"], "description": "Defaults to html."},
            "askReceipt": {"type": "string", "enum": ["yes", "no"], "description": "Request a read receipt. Defaults to no."},
            "encoding": {"type": "string", "description": "Character encoding. Defaults to UTF-8."},
            "inReplyTo": {"type": "string", "description": "Optional Message-ID header when saving a reply draft."},
            "refHeader": {"type": "string", "description": "Optional space-separated Message-ID references when saving a reply draft."},
        }
        return [
            self.function("zoho_mail_check_config", "Check whether Zoho Mail is configured.", {}),
            self.function("zoho_mail_list_accounts", "List Zoho Mail accounts available to the authenticated user.", {}),
            self.function("zoho_mail_list_folders", "List folders for the configured Zoho Mail account.", {}),
            self.function(
                "zoho_mail_list_messages",
                "List messages from a Zoho Mail folder.",
                {
                    "folderId": {"type": "string", "description": "Zoho Mail folder ID."},
                    "start": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "status": {"type": "string", "description": "Optional status filter, such as unread."},
                },
                ["folderId"],
            ),
            self.function(
                "zoho_mail_search_messages",
                "Search messages in Zoho Mail using Zoho Mail search syntax.",
                {
                    "searchKey": {"type": "string", "description": "Example: from:person@example.com or newMails."},
                    "start": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                ["searchKey"],
            ),
            self.function(
                "zoho_mail_get_message_content",
                "Read the content of a specific Zoho Mail message.",
                {
                    "folderId": {"type": "string"},
                    "messageId": {"type": "string"},
                    "includeBlockContent": {"type": "boolean"},
                },
                ["folderId", "messageId"],
            ),
            self.function(
                "zoho_mail_save_draft",
                "Save an email draft in Zoho Mail.",
                {**email_fields, "mode": {"type": "string", "enum": ["draft", "template"]}},
                ["toAddress", "subject", "content"],
            ),
            self.function(
                "zoho_mail_send_email",
                "Send an email from the configured Zoho Mail address.",
                email_fields,
                ["toAddress", "subject", "content"],
            ),
            self.function(
                "zoho_mail_reply",
                "Send a reply to an existing Zoho Mail message.",
                {"messageId": {"type": "string"}, **email_fields},
                ["messageId", "toAddress", "content"],
            ),
        ]

    def _config(self) -> Dict[str, Any]:
        merged = self.load_external_credentials()
        for key, value in self.credentials.items():
            if value is not None and value != "":
                merged[key] = value
        dc = merged.get("dc") or "com"
        hosts = DC_HOSTS.get(dc, DC_HOSTS["com"])
        merged.setdefault("dc", dc)
        merged.setdefault("accounts_base", hosts["accounts"])
        merged.setdefault("api_base", hosts["mail"])
        return merged

    def _missing_auth(self, config: Dict[str, Any]) -> List[str]:
        fields = {
            "client_id": "OAuth Client ID",
            "client_secret": "OAuth Client Secret",
            "refresh_token": "Refresh Token",
        }
        return [label for key, label in fields.items() if not config.get(key)]

    def _missing_config(self, config: Dict[str, Any]) -> List[str]:
        fields = {
            "account_id": "Zoho Mail Account ID",
            "from_address": "From Address",
        }
        return [label for key, label in fields.items() if not config.get(key)]

    def _access_token(self, config: Dict[str, Any]) -> str:
        missing = self._missing_auth(config)
        if missing:
            raise RuntimeError(f"Missing Zoho Mail credentials: {', '.join(missing)}")
        response = requests.post(
            f"{config['accounts_base'].rstrip('/')}/oauth/v2/token",
            data={
                "refresh_token": config["refresh_token"],
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
        if not response.ok:
            raise RuntimeError(f"Zoho OAuth token refresh failed (HTTP {response.status_code}): {response.text}")
        token = response.json().get("access_token")
        if not token:
            raise RuntimeError(f"Zoho OAuth response did not include access_token: {response.text}")
        return token

    def _zoho_request(self, method: str, path: str, *, query: Dict[str, Any] = None, body: Dict[str, Any] = None, requires_account: bool = True) -> Any:
        config = self._config()
        if requires_account and not config.get("account_id"):
            raise RuntimeError("Missing Zoho Mail account ID. Add it in Tools settings.")
        token = self._access_token(config)
        url = f"{config['api_base'].rstrip('/')}{path}"
        headers = {"Accept": "application/json", "Authorization": f"Zoho-oauthtoken {token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        params = {key: value for key, value in (query or {}).items() if value not in (None, "")}
        response = requests.request(method, url, headers=headers, params=params, json=body, timeout=20)
        response.raise_for_status()
        if not response.text:
            return {}
        try:
            return response.json()
        except ValueError:
            return response.text

    def _required(self, parameters: Dict[str, Any], key: str) -> Any:
        value = parameters.get(key)
        if value is None or value == "":
            raise RuntimeError(f"Missing required argument: {key}")
        return value

    def _email_body(self, parameters: Dict[str, Any], defaults: Dict[str, Any] = None) -> Dict[str, Any]:
        config = self._config()
        body = {
            **(defaults or {}),
            "fromAddress": parameters.get("fromAddress") or config.get("from_address"),
            "toAddress": self._required(parameters, "toAddress"),
            "subject": parameters.get("subject") or "",
            "content": self._required(parameters, "content"),
            "mailFormat": parameters.get("mailFormat") or "html",
            "encoding": parameters.get("encoding") or "UTF-8",
        }
        for optional in ["ccAddress", "bccAddress", "askReceipt", "mode", "inReplyTo", "refHeader"]:
            if parameters.get(optional):
                body[optional] = parameters[optional]
        if not body.get("fromAddress"):
            raise RuntimeError("Missing sender address. Add a From Address in Tools settings.")
        return body

    def _dump(self, value: Any) -> str:
        return json.dumps(value, indent=2, ensure_ascii=False)

    def execute(self, function_name: str, parameters: Dict[str, Any]) -> str:
        parameters = parameters or {}
        try:
            config = self._config()
            if function_name == "zoho_mail_check_config":
                missing = self._missing_auth(config) + self._missing_config(config)
                return self._dump(
                    {
                        "configured": not missing,
                        "missing": missing,
                        "dc": config.get("dc"),
                        "apiBase": config.get("api_base"),
                        "accountsBase": config.get("accounts_base"),
                    }
                )
            if function_name == "zoho_mail_list_accounts":
                return self._dump(self._zoho_request("GET", "/api/accounts", requires_account=False))
            if function_name == "zoho_mail_list_folders":
                return self._dump(self._zoho_request("GET", f"/api/accounts/{config['account_id']}/folders"))
            if function_name == "zoho_mail_list_messages":
                return self._dump(
                    self._zoho_request(
                        "GET",
                        f"/api/accounts/{config['account_id']}/messages/view",
                        query={
                            "folderId": self._required(parameters, "folderId"),
                            "start": parameters.get("start", 0),
                            "limit": parameters.get("limit", 20),
                            "status": parameters.get("status"),
                        },
                    )
                )
            if function_name == "zoho_mail_search_messages":
                return self._dump(
                    self._zoho_request(
                        "GET",
                        f"/api/accounts/{config['account_id']}/messages/search",
                        query={
                            "searchKey": self._required(parameters, "searchKey"),
                            "start": parameters.get("start", 0),
                            "limit": parameters.get("limit", 20),
                        },
                    )
                )
            if function_name == "zoho_mail_get_message_content":
                return self._dump(
                    self._zoho_request(
                        "GET",
                        (
                            f"/api/accounts/{config['account_id']}/folders/"
                            f"{self._required(parameters, 'folderId')}/messages/"
                            f"{self._required(parameters, 'messageId')}/content"
                        ),
                        query={"includeBlockContent": parameters.get("includeBlockContent")},
                    )
                )
            if function_name == "zoho_mail_save_draft":
                return self._dump(
                    self._zoho_request(
                        "POST",
                        f"/api/accounts/{config['account_id']}/messages",
                        body=self._email_body(parameters, {"mode": parameters.get("mode") or "draft"}),
                    )
                )
            if function_name == "zoho_mail_send_email":
                return self._dump(
                    self._zoho_request(
                        "POST",
                        f"/api/accounts/{config['account_id']}/messages",
                        body=self._email_body(parameters),
                    )
                )
            if function_name == "zoho_mail_reply":
                return self._dump(
                    self._zoho_request(
                        "POST",
                        f"/api/accounts/{config['account_id']}/messages/{self._required(parameters, 'messageId')}",
                        body=self._email_body(parameters),
                    )
                )
        except Exception as exc:
            return f"Zoho Mail tool error: {exc}"
        return f"Unknown Zoho Mail function: {function_name}"

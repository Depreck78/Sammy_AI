import json
from typing import Any, Dict, List

import requests

from app.tooling import BaseTool


REGION_BASES = {
    "us": "https://www.zohoapis.com",
    "eu": "https://www.zohoapis.eu",
    "in": "https://www.zohoapis.in",
    "au": "https://www.zohoapis.com.au",
    "jp": "https://www.zohoapis.jp",
    "ca": "https://www.zohoapis.ca",
}


class ZohoCrmTool(BaseTool):
    name = "zoho_crm"
    display_name = "Zoho CRM"
    description = "List, create, and update Zoho CRM contacts and deals."
    icon = "BriefcaseBusiness"
    requires_auth = True

    def get_auth_fields(self) -> List[Dict[str, Any]]:
        return [
            {"name": "region", "label": "Region", "type": "select", "options": ["us", "eu", "in", "au", "jp", "ca"]},
            {"name": "client_id", "label": "OAuth Client ID", "type": "text"},
            {"name": "client_secret", "label": "OAuth Client Secret", "type": "password"},
            {"name": "access_token", "label": "Access Token", "type": "password"},
            {"name": "refresh_token", "label": "Refresh Token", "type": "password"},
        ]

    def validate_auth(self, credentials: Dict[str, Any]) -> bool:
        return bool(credentials.get("access_token") or credentials.get("refresh_token"))

    def _base(self) -> str:
        return REGION_BASES.get(self.credentials.get("region") or "us", REGION_BASES["us"])

    def _headers(self) -> Dict[str, str]:
        token = self.credentials.get("access_token")
        if not token:
            raise RuntimeError("Zoho CRM is not connected. Add OAuth tokens in Tool settings.")
        return {"Authorization": f"Zoho-oauthtoken {token}"}

    def get_functions(self) -> List[Dict[str, Any]]:
        return [
            self.function("zoho_crm_list_contacts", "List Zoho CRM contacts.", {"limit": {"type": "integer"}}),
            self.function("zoho_crm_get_contact", "Get a Zoho CRM contact.", {"id": {"type": "string"}}, ["id"]),
            self.function("zoho_crm_create_contact", "Create a Zoho CRM contact.", {"data": {"type": "object"}}, ["data"]),
            self.function("zoho_crm_list_deals", "List Zoho CRM deals.", {"limit": {"type": "integer"}}),
            self.function("zoho_crm_create_deal", "Create a Zoho CRM deal.", {"data": {"type": "object"}}, ["data"]),
            self.function(
                "zoho_crm_update_record",
                "Update any Zoho CRM module record.",
                {
                    "module": {"type": "string"},
                    "id": {"type": "string"},
                    "data": {"type": "object"},
                },
                ["module", "id", "data"],
            ),
        ]

    def _get_module(self, module: str, limit: int = 25) -> str:
        response = requests.get(
            f"{self._base()}/crm/v2/{module}",
            headers=self._headers(),
            params={"per_page": limit},
            timeout=15,
        )
        response.raise_for_status()
        return json.dumps(response.json().get("data", []), indent=2)

    def execute(self, function_name: str, parameters: Dict[str, Any]) -> str:
        try:
            if function_name == "zoho_crm_list_contacts":
                return self._get_module("Contacts", int(parameters.get("limit") or 25))
            if function_name == "zoho_crm_get_contact":
                response = requests.get(
                    f"{self._base()}/crm/v2/Contacts/{parameters['id']}",
                    headers=self._headers(),
                    timeout=15,
                )
                response.raise_for_status()
                return json.dumps(response.json().get("data", []), indent=2)
            if function_name == "zoho_crm_create_contact":
                response = requests.post(
                    f"{self._base()}/crm/v2/Contacts",
                    headers=self._headers(),
                    json={"data": [parameters.get("data") or {}]},
                    timeout=15,
                )
                response.raise_for_status()
                return json.dumps(response.json(), indent=2)
            if function_name == "zoho_crm_list_deals":
                return self._get_module("Deals", int(parameters.get("limit") or 25))
            if function_name == "zoho_crm_create_deal":
                response = requests.post(
                    f"{self._base()}/crm/v2/Deals",
                    headers=self._headers(),
                    json={"data": [parameters.get("data") or {}]},
                    timeout=15,
                )
                response.raise_for_status()
                return json.dumps(response.json(), indent=2)
            if function_name == "zoho_crm_update_record":
                response = requests.put(
                    f"{self._base()}/crm/v2/{parameters['module']}/{parameters['id']}",
                    headers=self._headers(),
                    json={"data": [parameters.get("data") or {}]},
                    timeout=15,
                )
                response.raise_for_status()
                return json.dumps(response.json(), indent=2)
        except Exception as exc:
            return f"Zoho CRM tool error: {exc}"
        return f"Unknown Zoho CRM function: {function_name}"

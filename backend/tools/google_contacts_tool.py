import json
import re
import unicodedata
from typing import Any, Dict, List

import requests

from app.tooling import BaseTool


CONTACT_READ_MASK = "names,emailAddresses,phoneNumbers,organizations"
CONNECTIONS_URL = "https://people.googleapis.com/v1/people/me/connections"
SEARCH_URL = "https://people.googleapis.com/v1/people:searchContacts"
CONNECTIONS_PAGE_SIZE = 100
FIND_FALLBACK_LIMIT = 500


class GoogleContactsTool(BaseTool):
    name = "google_contacts"
    display_name = "Google Contacts"
    description = "List, find, create, and update Google contacts."
    icon = "ContactRound"
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

    def _headers(self) -> Dict[str, str]:
        token = self.credentials.get("access_token")
        if not token:
            raise RuntimeError("Google Contacts is not connected. Add OAuth tokens in Tool settings.")
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def get_functions(self) -> List[Dict[str, Any]]:
        return [
            self.function(
                "google_contacts_list_contacts",
                "List Google contacts.",
                {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
            ),
            self.function(
                "google_contacts_find_contact",
                "Search Google contacts by name.",
                {"name": {"type": "string"}},
                ["name"],
            ),
            self.function(
                "google_contacts_create_contact",
                "Create a Google contact.",
                {
                    "given_name": {"type": "string"},
                    "family_name": {"type": "string"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "company": {"type": "string"},
                },
                ["given_name"],
            ),
            self.function(
                "google_contacts_update_contact",
                "Update a Google contact by resource name.",
                {
                    "contact_id": {"type": "string"},
                    "fields": {"type": "object"},
                },
                ["contact_id", "fields"],
            ),
        ]

    def _display_name(self, names: List[Dict[str, Any]]) -> str:
        if not names:
            return ""
        primary = names[0]
        display = primary.get("displayName") or primary.get("unstructuredName")
        if display:
            return display
        return " ".join(part for part in [primary.get("givenName"), primary.get("familyName")] if part)

    def _normalize_person(self, person: Dict[str, Any]) -> Dict[str, Any]:
        names = person.get("names") or [{}]
        emails = person.get("emailAddresses") or []
        phones = person.get("phoneNumbers") or []
        orgs = person.get("organizations") or []
        return {
            "id": person.get("resourceName"),
            "name": self._display_name(names),
            "emails": [item.get("value", "") for item in emails],
            "phones": [item.get("value", "") for item in phones],
            "organizations": [item.get("name", "") for item in orgs],
        }

    def _request_connections(self, limit: int) -> List[Dict[str, Any]]:
        people: List[Dict[str, Any]] = []
        page_token = ""
        remaining = max(1, limit)
        while remaining > 0:
            params = {
                "pageSize": min(CONNECTIONS_PAGE_SIZE, remaining),
                "personFields": CONTACT_READ_MASK,
            }
            if page_token:
                params["pageToken"] = page_token
            response = requests.get(
                CONNECTIONS_URL,
                headers=self._headers(),
                params=params,
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            connections = data.get("connections", [])
            people.extend(connections)
            remaining = limit - len(people)
            page_token = data.get("nextPageToken") or ""
            if not page_token or not connections:
                break
        return people[:limit]

    def _search_contacts(self, query: str) -> List[Dict[str, Any]]:
        response = requests.get(
            SEARCH_URL,
            headers=self._headers(),
            params={
                "query": query,
                "readMask": CONTACT_READ_MASK,
                "pageSize": 30,
            },
            timeout=15,
        )
        response.raise_for_status()
        return [item.get("person", {}) for item in response.json().get("results", [])]

    def _compact_key(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        asciiish = "".join(char for char in normalized if not unicodedata.combining(char))
        return re.sub(r"[^0-9a-z]+", "", asciiish.casefold())

    def _name_candidates(self, person: Dict[str, Any]) -> List[str]:
        candidates = []
        for name in person.get("names") or []:
            candidates.extend(
                [
                    name.get("displayName", ""),
                    name.get("unstructuredName", ""),
                    name.get("givenName", ""),
                    name.get("familyName", ""),
                    " ".join(part for part in [name.get("givenName"), name.get("familyName")] if part),
                ]
            )
        for email in person.get("emailAddresses") or []:
            value = email.get("value", "")
            candidates.extend([value, value.split("@", 1)[0]])
        return [candidate for candidate in candidates if candidate]

    def _local_match_score(self, person: Dict[str, Any], query_key: str) -> int:
        scores = []
        for candidate in self._name_candidates(person):
            candidate_key = self._compact_key(candidate)
            if not candidate_key:
                continue
            if candidate_key == query_key:
                scores.append(0)
            elif candidate_key.startswith(query_key):
                scores.append(1)
            elif query_key in candidate_key:
                scores.append(2)
        return min(scores) if scores else 99

    def _local_find_contacts(self, query: str, people: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        query_key = self._compact_key(query)
        if not query_key:
            return []
        matches = [
            (self._local_match_score(person, query_key), index, person)
            for index, person in enumerate(people)
        ]
        matches = [match for match in matches if match[0] < 99]
        matches.sort(key=lambda item: (item[0], item[1]))
        return [person for _, _, person in matches]

    def execute(self, function_name: str, parameters: Dict[str, Any]) -> str:
        try:
            if function_name == "google_contacts_list_contacts":
                limit = int(parameters.get("limit") or 25)
                people = self._request_connections(limit)
                return json.dumps([self._normalize_person(person) for person in people], indent=2)

            if function_name == "google_contacts_find_contact":
                name = parameters["name"]
                self._search_contacts("")
                people = self._search_contacts(name)
                if not people:
                    people = self._local_find_contacts(name, self._request_connections(FIND_FALLBACK_LIMIT))
                results = [self._normalize_person(person) for person in people]
                return json.dumps(results, indent=2)

            if function_name == "google_contacts_create_contact":
                body = {
                    "names": [
                        {
                            "givenName": parameters.get("given_name", ""),
                            "familyName": parameters.get("family_name", ""),
                        }
                    ],
                    "emailAddresses": [{"value": parameters.get("email", "")}],
                    "phoneNumbers": [{"value": parameters.get("phone", "")}],
                    "organizations": [{"name": parameters.get("company", "")}],
                }
                response = requests.post(
                    "https://people.googleapis.com/v1/people:createContact",
                    headers=self._headers(),
                    json=body,
                    timeout=15,
                )
                response.raise_for_status()
                return json.dumps(self._normalize_person(response.json()), indent=2)

            if function_name == "google_contacts_update_contact":
                contact_id = parameters["contact_id"]
                fields = parameters.get("fields") or {}
                response = requests.patch(
                    f"https://people.googleapis.com/v1/{contact_id}:updateContact",
                    headers=self._headers(),
                    params={"updatePersonFields": ",".join(fields.keys())},
                    json=fields,
                    timeout=15,
                )
                response.raise_for_status()
                return json.dumps(self._normalize_person(response.json()), indent=2)
        except Exception as exc:
            return f"Google Contacts tool error: {exc}"
        return f"Unknown Google Contacts function: {function_name}"

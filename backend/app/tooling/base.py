from abc import ABC, abstractmethod
from typing import Any, Dict, List


class BaseTool(ABC):
    name: str = ""
    display_name: str = ""
    description: str = ""
    icon: str = "Wrench"
    requires_auth: bool = False

    def __init__(self, credentials: Dict[str, Any] = None):
        self.credentials = credentials or {}

    def function(
        self,
        name: str,
        description: str,
        properties: Dict[str, Any],
        required: List[str] = None,
    ) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required or [],
                },
            },
        }

    @abstractmethod
    def get_functions(self) -> List[Dict[str, Any]]:
        """Return OpenAI/Ollama-style tool definitions."""

    @abstractmethod
    def execute(self, function_name: str, parameters: Dict[str, Any]) -> str:
        """Execute a named function and return a text result."""

    def get_auth_fields(self) -> List[Dict[str, Any]]:
        return []

    def validate_auth(self, credentials: Dict[str, Any]) -> bool:
        if not self.requires_auth:
            return True
        return bool(credentials)

    def metadata(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name or self.name.replace("_", " ").title(),
            "description": self.description,
            "icon": self.icon,
            "requires_auth": self.requires_auth,
            "auth_fields": self.get_auth_fields(),
            "functions": self.get_functions(),
        }

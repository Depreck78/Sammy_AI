import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import codex_plugins, db, plugin_builder


def sample_operations():
    return [
        {
            "name": "list_items",
            "description": "List items from the approved service.",
            "method": "GET",
            "path": "/v1/items",
            "input_schema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
            },
            "query_params": ["limit"],
            "body_params": [],
        }
    ]


class PluginBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.plugin_home = root / "plugins"
        self.patches = [
            patch.object(db, "DB_PATH", root / "sammy.db"),
            patch.object(plugin_builder, "SAMMY_PLUGIN_HOME", self.plugin_home),
            patch.object(codex_plugins, "SAMMY_PLUGIN_HOME", self.plugin_home),
            patch.object(codex_plugins, "LOCAL_PLUGIN_HOME", root / "other-plugins"),
            patch.object(codex_plugins, "INCLUDE_CODEX_CACHE", False),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        db.init_db()
        self.agent = db.get_agent("default")
        self.conversation = db.create_conversation("Plugin builder", "model", self.agent["id"])

    def tearDown(self):
        self.temp_dir.cleanup()

    def propose(self, auth_type="none"):
        db.add_message(self.conversation["id"], "user", "Please build a plugin for Example Service")
        result = plugin_builder.handle_call(
            self.conversation["id"],
            self.agent,
            "Connect this service",
            plugin_builder.PROPOSE_FUNCTION_NAME,
            {
                "service_name": "Example Service",
                "goal": "List items from my account",
                "capabilities": ["Read account items"],
                "base_url": "https://api.example.com",
                "documentation_url": "https://docs.example.com/api",
                "api_type": "http",
                "auth_type": auth_type,
                "write_access": False,
                "allow_private_network": False,
            },
        )
        self.assertTrue(result["ok"])
        return result

    def build(self, proposal_id, approval_text):
        db.add_message(self.conversation["id"], "user", approval_text)
        return plugin_builder.handle_call(
            self.conversation["id"],
            self.agent,
            approval_text,
            plugin_builder.BUILD_FUNCTION_NAME,
            {
                "proposal_id": proposal_id,
                "plugin_name": "example-service",
                "display_name": "Example Service",
                "description": "Read items from Example Service.",
                "operations": sample_operations(),
            },
        )

    def test_build_requires_explicit_later_approval(self):
        proposal = self.propose()

        result = self.build(proposal["proposal_id"], "Tell me more about it")

        self.assertFalse(result["ok"])
        self.assertIn("did not explicitly approve", result["content"])
        self.assertFalse((self.plugin_home / "example-service").exists())
        self.assertEqual("pending", db.get_plugin_proposal(proposal["proposal_id"])["status"])

    def test_build_cannot_use_approval_words_from_proposal_turn(self):
        proposal = self.propose()

        result = plugin_builder.handle_call(
            self.conversation["id"],
            self.agent,
            "Please build this plugin",
            plugin_builder.BUILD_FUNCTION_NAME,
            {
                "proposal_id": proposal["proposal_id"],
                "plugin_name": "example-service",
                "display_name": "Example Service",
                "description": "Read items from Example Service.",
                "operations": sample_operations(),
            },
        )

        self.assertFalse(result["ok"])
        self.assertIn("new user message", result["content"])
        self.assertFalse((self.plugin_home / "example-service").exists())

    def test_approved_build_is_discoverable_and_enabled(self):
        proposal = self.propose()

        result = self.build(proposal["proposal_id"], "Yes, build it")

        self.assertTrue(result["ok"])
        self.assertEqual("built", db.get_plugin_proposal(proposal["proposal_id"])["status"])
        plugin_path = self.plugin_home / "example-service"
        self.assertTrue((plugin_path / ".sammy-plugin" / "plugin.json").exists())
        self.assertTrue((plugin_path / "server.py").exists())
        self.assertIn("sammy_plugin__example_service", db.get_agent(self.agent["id"])["enabled_tools"])

        plugins = codex_plugins.discover_codex_plugins()
        self.assertEqual(["example-service"], [item.name for item in plugins])
        tool = codex_plugins.CodexPluginTool(plugins[0])
        definitions = tool.get_functions()
        self.assertEqual(1, len(definitions))
        self.assertIn("list_items", definitions[0]["function"]["name"])

    def test_tool_build_mode_can_build_public_read_only_tool_in_same_turn(self):
        conversation = db.create_conversation("Build a tool", "model", self.agent["id"], "tool_builder")
        db.add_message(conversation["id"], "user", "Create a tool that lists Example Service items")
        proposal = plugin_builder.handle_call(
            conversation["id"],
            self.agent,
            "Create a tool that lists Example Service items",
            plugin_builder.PROPOSE_FUNCTION_NAME,
            {
                "service_name": "Example Service",
                "goal": "List items from my account",
                "capabilities": ["Read account items"],
                "base_url": "https://api.example.com",
                "documentation_url": "https://docs.example.com/api",
                "api_type": "http",
                "auth_type": "none",
                "write_access": False,
                "allow_private_network": False,
            },
            build_mode=True,
        )

        result = plugin_builder.handle_call(
            conversation["id"],
            self.agent,
            "Create a tool that lists Example Service items",
            plugin_builder.BUILD_FUNCTION_NAME,
            {
                "proposal_id": proposal["proposal_id"],
                "plugin_name": "example-service",
                "display_name": "Example Service",
                "description": "Read items from Example Service.",
                "operations": sample_operations(),
            },
            build_mode=True,
        )

        self.assertTrue(result["ok"])
        self.assertTrue((self.plugin_home / "example-service").exists())
        self.assertIn("sammy_plugin__example_service", db.get_agent(self.agent["id"])["enabled_tools"])
        self.assertEqual("tool_builder", db.get_conversation(conversation["id"])["conversation"]["mode"])

    def test_tool_build_mode_still_requires_follow_up_for_write_access(self):
        conversation = db.create_conversation("Build a tool", "model", self.agent["id"], "tool_builder")
        db.add_message(conversation["id"], "user", "Create a tool that updates Example Service items")
        proposal = plugin_builder.handle_call(
            conversation["id"],
            self.agent,
            "Create a tool that updates Example Service items",
            plugin_builder.PROPOSE_FUNCTION_NAME,
            {
                "service_name": "Example Service",
                "goal": "Update items in my account",
                "capabilities": ["Update account items"],
                "base_url": "https://api.example.com",
                "documentation_url": "https://docs.example.com/api",
                "api_type": "http",
                "auth_type": "none",
                "write_access": True,
                "allow_private_network": False,
            },
            build_mode=True,
        )

        result = plugin_builder.handle_call(
            conversation["id"],
            self.agent,
            "Create a tool that updates Example Service items",
            plugin_builder.BUILD_FUNCTION_NAME,
            {
                "proposal_id": proposal["proposal_id"],
                "plugin_name": "example-writer",
                "display_name": "Example Writer",
                "description": "Update items in Example Service.",
                "operations": [{**sample_operations()[0], "method": "POST"}],
            },
            build_mode=True,
        )

        self.assertFalse(result["ok"])
        self.assertIn("new user message", result["content"])
        self.assertFalse((self.plugin_home / "example-writer").exists())

    def test_generated_auth_fields_do_not_store_secrets_in_plugin(self):
        proposal = self.propose(auth_type="api_key")

        result = self.build(proposal["proposal_id"], "Approved, please build this plugin")

        self.assertTrue(result["ok"])
        plugin_path = self.plugin_home / "example-service"
        combined = "\n".join(path.read_text(encoding="utf-8") for path in plugin_path.rglob("*") if path.is_file())
        self.assertIn('"name": "api_key"', combined)
        self.assertNotIn("secret-test-value", combined)
        plugin = codex_plugins.discover_codex_plugins()[0]
        self.assertTrue(plugin.auth_fields)

    def test_unapproved_private_destination_is_rejected(self):
        result = plugin_builder.handle_call(
            self.conversation["id"],
            self.agent,
            "Connect local app",
            plugin_builder.PROPOSE_FUNCTION_NAME,
            {
                "service_name": "Local App",
                "goal": "Read local data",
                "capabilities": ["Read data"],
                "base_url": "http://127.0.0.1:9999",
                "documentation_url": "https://docs.example.com/local-api",
                "api_type": "http",
                "auth_type": "none",
                "write_access": False,
                "allow_private_network": False,
            },
        )

        self.assertFalse(result["ok"])
        self.assertIn("HTTPS", result["content"])

    def test_read_only_approval_blocks_write_operations(self):
        proposal = self.propose()
        db.add_message(self.conversation["id"], "user", "Yes, build it")
        write_operation = sample_operations()[0]
        write_operation = {**write_operation, "method": "DELETE"}

        result = plugin_builder.handle_call(
            self.conversation["id"],
            self.agent,
            "Yes, build it",
            plugin_builder.BUILD_FUNCTION_NAME,
            {
                "proposal_id": proposal["proposal_id"],
                "plugin_name": "example-service",
                "display_name": "Example Service",
                "description": "Read items from Example Service.",
                "operations": [write_operation],
            },
        )

        self.assertFalse(result["ok"])
        self.assertIn("read-only", result["content"])
        self.assertFalse((self.plugin_home / "example-service").exists())

    def test_mail_protocol_proposal_is_rejected(self):
        db.add_message(self.conversation["id"], "user", "Build a PrivateEmail tool that reads and sends mail")

        result = plugin_builder.handle_call(
            self.conversation["id"],
            self.agent,
            "Build a PrivateEmail tool that reads and sends mail",
            plugin_builder.PROPOSE_FUNCTION_NAME,
            {
                "service_name": "PrivateEmail Namecheap API",
                "goal": "Read emails from inboxes using IMAP and send new emails via SMTP.",
                "capabilities": ["Read folders with IMAP", "Send mail with SMTP"],
                "base_url": "https://mail.privateemail.com",
                "documentation_url": "https://www.namecheap.com/support/knowledgebase/article.aspx/1179/2175/general-private-email-configuration-for-mail-clients-and-mobile-devices/",
                "api_type": "http",
                "auth_type": "basic",
                "write_access": True,
                "allow_private_network": False,
            },
        )

        self.assertFalse(result["ok"])
        self.assertIn("reviewed adapter/template", result["content"])
        self.assertFalse(db.latest_plugin_proposal(self.conversation["id"]))

    def test_unsupported_api_type_is_rejected(self):
        db.add_message(self.conversation["id"], "user", "Build an IMAP tool")

        result = plugin_builder.handle_call(
            self.conversation["id"],
            self.agent,
            "Build an IMAP tool",
            plugin_builder.PROPOSE_FUNCTION_NAME,
            {
                "service_name": "PrivateEmail",
                "goal": "Read mail with IMAP",
                "capabilities": ["Read mail"],
                "base_url": "https://mail.privateemail.com",
                "documentation_url": "https://example.com/mail-client-settings",
                "api_type": "unsupported",
                "auth_type": "basic",
                "write_access": False,
                "allow_private_network": False,
            },
        )

        self.assertFalse(result["ok"])
        self.assertIn("supports only documented HTTP JSON APIs", result["content"])

    def test_build_rejects_fake_protocol_http_paths(self):
        proposal = self.propose()
        db.add_message(self.conversation["id"], "user", "Yes, build it")
        operation = {
            **sample_operations()[0],
            "name": "list_imap_messages",
            "description": "List mail messages with IMAP.",
            "path": "/imap/list/{folder_name}",
            "input_schema": {
                "type": "object",
                "properties": {"folder_name": {"type": "string"}},
                "required": ["folder_name"],
            },
            "query_params": [],
            "body_params": [],
        }

        result = plugin_builder.handle_call(
            self.conversation["id"],
            self.agent,
            "Yes, build it",
            plugin_builder.BUILD_FUNCTION_NAME,
            {
                "proposal_id": proposal["proposal_id"],
                "plugin_name": "example-service",
                "display_name": "Example Service",
                "description": "Read items from Example Service.",
                "operations": [operation],
            },
        )

        self.assertFalse(result["ok"])
        self.assertIn("protocol-shaped fake HTTP path", result["content"])
        self.assertFalse((self.plugin_home / "example-service").exists())

    def test_build_rejects_path_params_missing_from_schema(self):
        proposal = self.propose()
        db.add_message(self.conversation["id"], "user", "Yes, build it")
        operation = {
            **sample_operations()[0],
            "path": "/v1/items/{item_id}",
        }

        result = plugin_builder.handle_call(
            self.conversation["id"],
            self.agent,
            "Yes, build it",
            plugin_builder.BUILD_FUNCTION_NAME,
            {
                "proposal_id": proposal["proposal_id"],
                "plugin_name": "example-service",
                "display_name": "Example Service",
                "description": "Read items from Example Service.",
                "operations": [operation],
            },
        )

        self.assertFalse(result["ok"])
        self.assertIn("path parameter", result["content"])
        self.assertFalse((self.plugin_home / "example-service").exists())

    def test_invalid_generated_plugin_spec_is_not_discovered(self):
        plugin_path = self.plugin_home / "bad-privateemail"
        (plugin_path / ".sammy-plugin").mkdir(parents=True)
        (plugin_path / ".sammy-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "bad-privateemail",
                    "version": "0.1.0",
                    "description": "Broken generated mail client.",
                    "interface": {"displayName": "Bad PrivateEmail", "generatedBySammy": True},
                    "mcpServers": ".mcp.json",
                }
            ),
            encoding="utf-8",
        )
        (plugin_path / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"bad-privateemail": {"command": "python", "args": ["server.py"], "cwd": "."}}}),
            encoding="utf-8",
        )
        (plugin_path / "plugin-spec.json").write_text(
            json.dumps(
                {
                    "name": "bad-privateemail",
                    "base_url": "https://mail.privateemail.com",
                    "documentation_url": "https://example.com/mail-client-settings",
                    "auth": {"type": "basic"},
                    "operations": [
                        {
                            "name": "list_messages",
                            "description": "List IMAP messages.",
                            "method": "GET",
                            "path": "/imap/list/{folder_name}",
                            "input_schema": {
                                "type": "object",
                                "properties": {"folder_name": {"type": "string"}},
                                "required": ["folder_name"],
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual([], codex_plugins.discover_codex_plugins())


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import db, plugin_builder
from app.chat_jobs import ChatJob
from app.generation import (
    context_input_budget,
    estimate_messages_tokens,
    render_compaction_source,
    split_history_for_compaction,
    strip_continuation_overlap,
    tool_call_signature,
    tool_result_failed,
)
from app.main import ChatPayload, run_background_chat_job


class GenerationHelperTests(unittest.TestCase):
    def test_continuation_overlap_is_removed(self) -> None:
        existing = "The report ends with an important conclusion."
        continuation = "important conclusion. Next, the appendix begins."

        self.assertEqual(" Next, the appendix begins.", strip_continuation_overlap(existing, continuation))

    def test_context_split_keeps_recent_messages(self) -> None:
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "old request " * 200},
            {"role": "assistant", "content": "old answer " * 200},
            {"role": "user", "content": "current task"},
        ]
        system, older, recent = split_history_for_compaction(messages, recent_budget=80)

        self.assertEqual("system", system[0]["content"])
        self.assertEqual("current task", recent[-1]["content"])
        self.assertGreater(len(older), 0)
        self.assertGreater(estimate_messages_tokens(messages), context_input_budget(2048, 512, []))

    def test_compaction_source_keeps_recent_tail_when_clipped(self) -> None:
        source = render_compaction_source(
            [
                {"role": "user", "content": "rank both inboxes by priority"},
                {"role": "tool", "name": "gmail_list_emails", "content": "gmail item " * 500},
                {"role": "tool", "name": "zoho_mail_list_messages", "content": "critical zoho lead from Example Org"},
            ],
            max_chars=900,
        )

        self.assertIn("rank both inboxes by priority", source)
        self.assertIn("critical zoho lead from Example Org", source)
        self.assertIn("omitted", source)

    def test_tool_progress_signals_are_stable(self) -> None:
        left = tool_call_signature("search", {"query": "Sammy", "limit": 5})
        right = tool_call_signature("search", {"limit": 5, "query": "Sammy"})

        self.assertEqual(left, right)
        self.assertTrue(tool_result_failed("Web search tool error: timed out"))
        self.assertFalse(tool_result_failed("Found 5 results"))


class BackgroundGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_limited_response_automatically_continues(self) -> None:
        calls = 0
        saved_messages = []

        async def fake_chat_stream(model, messages, tools, options):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield {"message": {"content": "First half with an important conclusion."}}
                yield {"done": True, "done_reason": "length", "message": {}}
            else:
                yield {"message": {"content": "important conclusion. Then the task is complete."}}
                yield {"done": True, "done_reason": "stop", "message": {}}

        class FakeRegistry:
            def tool_definitions(self, enabled_tools):
                return []

            def plugin_injections(self, enabled_tools):
                return ""

        def fake_add_message(conversation_id, role, content, metadata=None):
            message = {
                "id": f"message-{len(saved_messages) + 1}",
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "created_at": "now",
            }
            saved_messages.append(message)
            return message

        job = ChatJob(conversation_id="conversation", agent_id="default", model="test-model")
        payload = ChatPayload(message="Complete the task", options={"num_ctx": 8192, "num_predict": 64})

        with TemporaryDirectory() as directory, patch("app.db.DB_PATH", Path(directory) / "sammy.sqlite"):
            db.init_db()
            with (
                patch("app.main.chat_stream", fake_chat_stream),
                patch("app.main.registry", return_value=FakeRegistry()),
                patch("app.main.db.list_chat_messages", return_value=[{"role": "user", "content": "Complete the task"}]),
                patch("app.main.db.add_message", side_effect=fake_add_message),
                patch("app.main.memory.memory_tool_definitions", return_value=[]),
                patch("app.main.memory.memory_context", return_value=""),
                patch("app.main.memory.schedule_review"),
            ):
                await run_background_chat_job(
                    job,
                    payload,
                    {"num_ctx": 8192, "num_predict": 64, "temperature": 0.2, "think": False},
                    {"id": "default", "system_prompt": "", "enabled_tools": []},
                    {"id": "conversation", "agent_id": "default", "model": "test-model"},
                    None,
                )

        self.assertEqual(2, calls)
        self.assertEqual("complete", job.status)
        self.assertEqual(
            "First half with an important conclusion. Then the task is complete.",
            saved_messages[-1]["content"],
        )
        self.assertEqual(2, saved_messages[-1]["metadata"]["continuation_parts"])
        self.assertTrue(any(event.data.get("phase") == "continuing" for event in job.events if event.event == "work_state"))

    async def test_missing_capability_can_create_proposal_but_not_plugin(self) -> None:
        calls = 0

        async def fake_chat_stream(model, messages, tools, options):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": plugin_builder.PROPOSE_FUNCTION_NAME,
                                    "arguments": {
                                        "service_name": "Example Service",
                                        "goal": "Read account items",
                                        "capabilities": ["Read account items"],
                                        "base_url": "https://api.example.com",
                                        "documentation_url": "https://docs.example.com/api",
                                        "api_type": "http",
                                        "auth_type": "api_key",
                                        "write_access": False,
                                        "allow_private_network": False,
                                    },
                                }
                            }
                        ]
                    }
                }
                yield {"done": True, "done_reason": "stop", "message": {}}
            else:
                yield {"message": {"content": "I can create that local plugin. It would read account items from api.example.com using an API key. Would you like me to build it?"}}
                yield {"done": True, "done_reason": "stop", "message": {}}

        class FakeRegistry:
            def tool_definitions(self, enabled_tools):
                return []

            def plugin_injections(self, enabled_tools):
                return ""

            def function_map(self, enabled_tools):
                return {}

        with TemporaryDirectory() as directory, patch("app.db.DB_PATH", Path(directory) / "sammy.sqlite"):
            db.init_db()
            agent = db.get_agent("default")
            conversation = db.create_conversation("Proposal", "test-model", agent["id"])
            user_message = db.add_message(conversation["id"], "user", "Can you connect Example Service?")
            job = ChatJob(conversation_id=conversation["id"], agent_id=agent["id"], model="test-model")
            payload = ChatPayload(
                conversation_id=conversation["id"],
                message="Can you connect Example Service?",
                options={"num_ctx": 8192, "num_predict": 512},
            )

            with (
                patch("app.main.chat_stream", fake_chat_stream),
                patch("app.main.registry", return_value=FakeRegistry()),
                patch("app.main.memory.memory_tool_definitions", return_value=[]),
                patch("app.main.memory.memory_context", return_value=""),
                patch("app.main.memory.schedule_review"),
            ):
                await run_background_chat_job(
                    job,
                    payload,
                    {"num_ctx": 8192, "num_predict": 512, "temperature": 0.2, "think": False},
                    agent,
                    conversation,
                    user_message,
                )

            proposal = db.latest_plugin_proposal(conversation["id"], status="pending")
            self.assertIsNotNone(proposal)
            self.assertEqual(user_message["id"], proposal["source_user_message_id"])
            self.assertEqual("complete", job.status)
            self.assertIn("Would you like me to build it?", job.final_message["content"])
            self.assertFalse((Path(directory) / "plugins").exists())

    async def test_unfinished_tool_narration_gets_explicit_continue_prompt(self) -> None:
        calls = 0
        executed = []

        async def fake_chat_stream(model, messages, tools, options):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield {"message": {"content": 'Got it! Let me search for "Jordan" in your Google Contacts now.'}}
            elif calls == 2:
                self.assertEqual("user", messages[-1]["role"])
                self.assertIn("call the appropriate enabled tool now", messages[-1]["content"])
                yield {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "google_contacts_find_contact",
                                    "arguments": {"name": "Jordan"},
                                }
                            }
                        ]
                    }
                }
            else:
                self.assertTrue(any(message["role"] == "tool" and "Jordan Rivera" in message["content"] for message in messages))
                yield {"message": {"content": "Found him: Jordan Rivera."}}
            yield {"done": True, "done_reason": "stop", "message": {}}

        class FakeTool:
            name = "google_contacts"
            display_name = "Google Contacts"
            requires_auth = False

            def validate_auth(self, credentials):
                return True

        class FakeRegistry:
            tool = FakeTool()

            def tool_definitions(self, enabled_tools):
                return [
                    {
                        "type": "function",
                        "function": {
                            "name": "google_contacts_find_contact",
                            "description": "Search Google contacts by name.",
                            "parameters": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                                "required": ["name"],
                            },
                        },
                    }
                ]

            def plugin_injections(self, enabled_tools):
                return ""

            def function_map(self, enabled_tools):
                return {"google_contacts_find_contact": (self.tool, "google_contacts_find_contact")}

            def get(self, name):
                return self.tool if name == "google_contacts" else None

            def execute(self, function_name, parameters, enabled_tools):
                executed.append((function_name, parameters))
                return {
                    "tool_name": "google_contacts",
                    "tool_display_name": "Google Contacts",
                    "function_name": function_name,
                    "content": '[{"name": "Jordan Rivera", "emails": ["jordan@example.com"]}]',
                }

        with TemporaryDirectory() as directory, patch("app.db.DB_PATH", Path(directory) / "sammy.sqlite"):
            db.init_db()
            agent = db.get_agent("email-manager")
            conversation = db.create_conversation("Find contact", "test-model", agent["id"])
            user_message = db.add_message(conversation["id"], "user", "Find Jordan in my contacts")
            job = ChatJob(conversation_id=conversation["id"], agent_id=agent["id"], model="test-model")
            payload = ChatPayload(
                conversation_id=conversation["id"],
                message="Find Jordan in my contacts",
                options={"num_ctx": 8192, "num_predict": 512},
            )

            with (
                patch("app.main.chat_stream", fake_chat_stream),
                patch("app.main.registry", return_value=FakeRegistry()),
                patch("app.main.memory.memory_tool_definitions", return_value=[]),
                patch("app.main.memory.memory_context", return_value=""),
                patch("app.main.memory.schedule_review"),
            ):
                await run_background_chat_job(
                    job,
                    payload,
                    {"num_ctx": 8192, "num_predict": 512, "temperature": 0.2, "think": False},
                    agent,
                    conversation,
                    user_message,
                )

        self.assertEqual(3, calls)
        self.assertEqual([("google_contacts_find_contact", {"name": "Jordan"})], executed)
        self.assertEqual("complete", job.status)
        self.assertEqual("Found him: Jordan Rivera.", job.final_message["content"])
        self.assertNotEqual("empty", job.final_message["metadata"].get("response_status"))

    async def test_lost_context_reply_after_tools_gets_retried(self) -> None:
        calls = 0

        async def fake_chat_stream(model, messages, tools, options):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "gmail_list_emails",
                                    "arguments": {"max_results": 10},
                                }
                            }
                        ]
                    }
                }
            elif calls == 2:
                yield {"message": {"content": "Hey, I'm ready to help you with whatever you need! What would you like me to work on?"}}
            else:
                self.assertEqual("user", messages[-1]["role"])
                self.assertIn("already used tools", messages[-1]["content"])
                yield {"message": {"content": "I found one urgent email and one low-priority newsletter."}}
            yield {"done": True, "done_reason": "stop", "message": {}}

        class FakeTool:
            name = "gmail"
            display_name = "Gmail"
            requires_auth = False

            def validate_auth(self, credentials):
                return True

        class FakeRegistry:
            tool = FakeTool()

            def tool_definitions(self, enabled_tools):
                return [
                    {
                        "type": "function",
                        "function": {
                            "name": "gmail_list_emails",
                            "description": "List Gmail messages.",
                            "parameters": {"type": "object", "properties": {"max_results": {"type": "integer"}}},
                        },
                    }
                ]

            def plugin_injections(self, enabled_tools):
                return ""

            def function_map(self, enabled_tools):
                return {"gmail_list_emails": (self.tool, "gmail_list_emails")}

            def get(self, name):
                return self.tool if name == "gmail" else None

            def execute(self, function_name, parameters, enabled_tools):
                return {
                    "tool_name": "gmail",
                    "tool_display_name": "Gmail",
                    "function_name": function_name,
                    "content": '[{"subject": "Urgent account issue"}, {"subject": "Newsletter"}]',
                }

        with TemporaryDirectory() as directory, patch("app.db.DB_PATH", Path(directory) / "sammy.sqlite"):
            db.init_db()
            agent = db.get_agent("email-manager")
            conversation = db.create_conversation("Rank mail", "test-model", agent["id"])
            user_message = db.add_message(conversation["id"], "user", "Rank my emails by priority")
            job = ChatJob(conversation_id=conversation["id"], agent_id=agent["id"], model="test-model")
            payload = ChatPayload(
                conversation_id=conversation["id"],
                message="Rank my emails by priority",
                options={"num_ctx": 8192, "num_predict": 512},
            )

            with (
                patch("app.main.chat_stream", fake_chat_stream),
                patch("app.main.registry", return_value=FakeRegistry()),
                patch("app.main.memory.memory_tool_definitions", return_value=[]),
                patch("app.main.memory.memory_context", return_value=""),
                patch("app.main.memory.schedule_review"),
            ):
                await run_background_chat_job(
                    job,
                    payload,
                    {"num_ctx": 8192, "num_predict": 512, "temperature": 0.2, "think": False},
                    agent,
                    conversation,
                    user_message,
                )

        self.assertEqual(3, calls)
        self.assertEqual("complete", job.status)
        self.assertEqual("I found one urgent email and one low-priority newsletter.", job.final_message["content"])

    async def test_tool_build_mode_builds_and_enables_public_read_only_tool(self) -> None:
        calls = 0

        async def fake_chat_stream(model, messages, tools, options):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.assertIn("TOOL BUILD MODE", messages[0]["content"])
                yield {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": plugin_builder.PROPOSE_FUNCTION_NAME,
                                    "arguments": {
                                        "service_name": "Example Service",
                                        "goal": "Read account items",
                                        "capabilities": ["Read account items"],
                                        "base_url": "https://api.example.com",
                                        "documentation_url": "https://docs.example.com/api",
                                        "api_type": "http",
                                        "auth_type": "none",
                                        "write_access": False,
                                        "allow_private_network": False,
                                    },
                                }
                            }
                        ]
                    }
                }
            elif calls == 2:
                proposal = db.latest_plugin_proposal(conversation["id"], status="pending")
                yield {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": plugin_builder.BUILD_FUNCTION_NAME,
                                    "arguments": {
                                        "proposal_id": proposal["id"],
                                        "plugin_name": "example-service",
                                        "display_name": "Example Service",
                                        "description": "Read items from Example Service.",
                                        "operations": [
                                            {
                                                "name": "list_items",
                                                "description": "List account items.",
                                                "method": "GET",
                                                "path": "/v1/items",
                                                "input_schema": {
                                                    "type": "object",
                                                    "properties": {"limit": {"type": "integer"}},
                                                },
                                                "query_params": ["limit"],
                                                "body_params": [],
                                            }
                                        ],
                                    },
                                }
                            }
                        ]
                    }
                }
            else:
                yield {"message": {"content": "The Example Service tool is built and enabled for this agent."}}
            yield {"done": True, "done_reason": "stop", "message": {}}

        class FakeRegistry:
            def tool_definitions(self, enabled_tools):
                return []

            def plugin_injections(self, enabled_tools):
                return ""

            def function_map(self, enabled_tools):
                return {}

        with TemporaryDirectory() as directory, patch(
            "app.db.DB_PATH", Path(directory) / "sammy.sqlite"
        ), patch.object(plugin_builder, "SAMMY_PLUGIN_HOME", Path(directory) / "plugins"):
            db.init_db()
            agent = db.get_agent("default")
            conversation = db.create_conversation("Build a tool", "test-model", agent["id"], "tool_builder")
            request = "Build a read-only tool for Example Service using its official API."
            user_message = db.add_message(conversation["id"], "user", request)
            job = ChatJob(conversation_id=conversation["id"], agent_id=agent["id"], model="test-model")
            payload = ChatPayload(
                conversation_id=conversation["id"],
                message=request,
                options={"num_ctx": 8192, "num_predict": 512},
            )

            with (
                patch("app.main.chat_stream", fake_chat_stream),
                patch("app.main.registry", return_value=FakeRegistry()),
                patch("app.main.memory.memory_tool_definitions", return_value=[]),
                patch("app.main.memory.memory_context", return_value=""),
                patch("app.main.memory.schedule_review"),
            ):
                await run_background_chat_job(
                    job,
                    payload,
                    {"num_ctx": 8192, "num_predict": 512, "temperature": 0.2, "think": False},
                    agent,
                    conversation,
                    user_message,
                )

            self.assertEqual(3, calls)
            self.assertEqual("complete", job.status)
            self.assertTrue((Path(directory) / "plugins" / "example-service").exists())
            self.assertIn("sammy_plugin__example_service", db.get_agent(agent["id"])["enabled_tools"])
            self.assertIn("built and enabled", job.final_message["content"])


if __name__ == "__main__":
    unittest.main()

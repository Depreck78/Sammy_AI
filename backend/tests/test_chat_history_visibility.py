import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db


class ChatHistoryVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = patch.object(db, "DB_PATH", Path(self.temp_dir.name) / "sammy.sqlite")
        self.db_path.start()
        self.addCleanup(self.db_path.stop)
        db.init_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_conversation_history_collapses_internal_tool_turns(self):
        conversation = db.create_conversation("Tool check", "model", "default")
        db.add_message(conversation["id"], "user", "Check that the tool works")
        db.add_message(
            conversation["id"],
            "assistant",
            "Let me check the plugin directory.",
            {"tool_call_round": 1, "job_id": "job"},
        )
        db.add_message(
            conversation["id"],
            "tool",
            "File system tool error: path is outside allowed directories.",
            {
                "tool_name": "filesystem",
                "tool_display_name": "File System",
                "function_name": "filesystem_list_directory",
            },
        )
        db.add_message(
            conversation["id"],
            "assistant",
            "Let me try from an allowed path.",
            {"tool_call_round": 2, "auto_continue": True, "job_id": "job"},
        )
        db.add_message(
            conversation["id"],
            "assistant",
            "The tool is installed, but the generated connector needs a real HTTP API.",
            {"finish_reason": "stop", "job_id": "job"},
        )

        messages = db.get_conversation(conversation["id"])["messages"]

        self.assertEqual(["user", "assistant"], [message["role"] for message in messages])
        self.assertEqual(
            "The tool is installed, but the generated connector needs a real HTTP API.",
            messages[1]["content"],
        )
        self.assertEqual(
            ["Let me check the plugin directory.", "Let me try from an allowed path."],
            messages[1]["metadata"]["progress_notes"],
        )
        self.assertEqual(1, len(messages[1]["metadata"]["tool_events"]))
        self.assertEqual("File System", messages[1]["metadata"]["tool_events"][0]["tool_display_name"])

    def test_orphaned_internal_trace_does_not_leak_into_next_prompt(self):
        conversation = db.create_conversation("Interrupted", "model", "default")
        db.add_message(conversation["id"], "user", "Start checking")
        db.add_message(
            conversation["id"],
            "assistant",
            "I will inspect the tool.",
            {"tool_call_round": 1},
        )
        db.add_message(
            conversation["id"],
            "tool",
            "Tool output",
            {"tool_name": "filesystem", "function_name": "filesystem_list_directory"},
        )
        db.add_message(conversation["id"], "user", "Different prompt")
        db.add_message(conversation["id"], "assistant", "Fresh answer", {"finish_reason": "stop"})

        messages = db.get_conversation(conversation["id"])["messages"]

        self.assertEqual(["user", "user", "assistant"], [message["role"] for message in messages])
        self.assertEqual("Fresh answer", messages[-1]["content"])
        self.assertNotIn("progress_notes", messages[-1]["metadata"])
        self.assertNotIn("tool_events", messages[-1]["metadata"])


if __name__ == "__main__":
    unittest.main()

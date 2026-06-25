import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, memory


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = patch.object(db, "DB_PATH", root / "sammy.db")
        self.memory_dir = patch.object(memory, "MEMORY_DIR", root / "memory")
        self.agent_dir = patch.object(memory, "AGENT_MEMORY_DIR", root / "memory" / "agents")
        self.soul_file = patch.object(memory, "SOUL_MEMORY_FILE", root / "memory" / "soul.md")
        self.user_file = patch.object(memory, "USER_MEMORY_FILE", root / "memory" / "user.md")
        for item in (self.db_path, self.memory_dir, self.agent_dir, self.soul_file, self.user_file):
            item.start()
            self.addCleanup(item.stop)
        db.init_db()
        self.agent = db.get_agent("default")

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_legacy_files_migrate_and_soul_is_locked_for_agent(self):
        memory.MEMORY_DIR.mkdir(parents=True)
        memory.SOUL_MEMORY_FILE.write_text("Keep the shared identity stable.", encoding="utf-8")
        memory.USER_MEMORY_FILE.write_text("The user prefers short answers.", encoding="utf-8")

        memory.initialize([self.agent])

        soul = db.list_memories(scope="soul", status="active")
        user = db.list_memories(scope="user", status="active")
        self.assertEqual(soul[0]["content"], "Keep the shared identity stable.")
        self.assertEqual(user[0]["content"], "The user prefers short answers.")
        result = memory.save_memory_from_call(
            self.agent,
            {"memory_file": "soul", "content": "Replace the identity."},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(len(db.list_memories(scope="soul", status="active")), 1)

    async def test_recall_uses_full_text_search_with_provenance(self):
        memory.initialize([self.agent])
        conversation = db.create_conversation("Python style", "model", self.agent["id"])
        db.add_message(conversation["id"], "user", "Please always use concise Python examples")
        entry = db.add_memory(
            {
                "scope": "user",
                "kind": "preference",
                "content": "Prefers concise Python examples.",
                "source_conversation_id": conversation["id"],
            }
        )

        context = memory.memory_context(self.agent, "Can you show a concise Python example?", "other-chat")

        self.assertIn("Prefers concise Python examples", context)
        self.assertIn("Python style", context)
        self.assertEqual(db.get_memory(entry["id"])["use_count"], 1)

    async def test_ask_mode_queues_post_turn_review_for_approval(self):
        memory.initialize([self.agent])
        db.update_settings({"memory_mode": "ask"})

        async def fake_stream(*_args, **_kwargs):
            yield {
                "message": {
                    "content": (
                        '{"memories":[{"scope":"user","kind":"preference",'
                        '"content":"Prefers examples in TypeScript.","confidence":0.92,'
                        '"sensitive":false,"expires_days":null}]}'
                    )
                },
                "done": True,
            }

        with patch.object(memory, "chat_stream", fake_stream):
            count = await memory.review_completed_turn(
                "local-model",
                self.agent,
                "conversation-id",
                "Please remember that I prefer examples in TypeScript.",
                "I will use TypeScript examples in the future.",
            )

        self.assertEqual(count, 1)
        pending = db.list_memories(status="pending")
        self.assertEqual(pending[0]["content"], "Prefers examples in TypeScript.")
        self.assertEqual(pending[0]["source_label"], "Post-turn local review")

    async def test_consolidation_archives_low_priority_entries_over_budget(self):
        memory.initialize([self.agent])
        for index in range(8):
            db.add_memory(
                {
                    "scope": "user",
                    "content": f"Durable project note {index}: " + ("detail " * 55),
                    "confidence": 0.6 + index * 0.04,
                }
            )

        result = memory.consolidate()
        active_chars = sum(len(item["content"]) + 2 for item in db.list_memories(scope="user", status="active"))

        self.assertGreater(result["archived"], 0)
        self.assertLessEqual(active_chars, memory.MAX_MEMORY_CHARS)


if __name__ == "__main__":
    unittest.main()

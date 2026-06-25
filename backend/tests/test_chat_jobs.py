import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from app import db
from app.chat_jobs import ChatJob, chat_job_manager
from app.main import app


class ChatJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_stream_replays_after_cursor_through_terminal_event(self) -> None:
        job = ChatJob(conversation_id="conversation", agent_id="default", model="model")
        await job.publish("token", {"content": "first"})
        await job.publish("token", {"content": "second"})
        await job.publish("done", {"message": {"id": "message"}})
        await job.set_terminal("complete", final_message={"id": "message"})

        chunks = [chunk async for chunk in job.event_stream(after=1)]

        self.assertEqual(2, len(chunks))
        self.assertIn("id: 2", chunks[0])
        self.assertIn('"second"', chunks[0])
        self.assertIn("event: done", chunks[1])

    async def test_chat_job_api_creates_and_replays_background_work(self) -> None:
        async def fake_models():
            return [{"name": "test-model"}]

        async def fake_runner(job, payload, settings, agent, conversation, user_message):
            job.status = "running"
            await job.set_work_state("writing", "Writing response")
            await job.publish("token", {"content": "done"})
            message = db.add_message(
                job.conversation_id,
                "assistant",
                "done",
                {"job_id": job.id, "response_status": "complete"},
            )
            await job.publish("assistant_message", message)
            await job.publish("done", {"message": message})
            await job.set_terminal("complete", final_message=message)

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "sammy.sqlite"
            with (
                patch("app.db.DB_PATH", database_path),
                patch("app.main.list_models", fake_models),
                patch("app.main.run_background_chat_job", fake_runner),
            ):
                db.init_db()
                conversation = db.create_conversation("Test", "test-model", "default")
                chat_job_manager.jobs.clear()
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    created = await client.post(
                        "/api/chat/jobs",
                        json={
                            "conversation_id": conversation["id"],
                            "message": "Finish this",
                            "model": "test-model",
                            "agent_id": "default",
                            "options": {"num_ctx": 4096, "num_predict": 256},
                        },
                    )
                    self.assertEqual(200, created.status_code)
                    job_id = created.json()["job"]["id"]
                    await asyncio.sleep(0)

                    status = await client.get(f"/api/chat/jobs/{job_id}")
                    replay = await client.get(f"/api/chat/jobs/{job_id}/stream?after=1")

                self.assertEqual("complete", status.json()["job"]["status"])
                self.assertIn("event: token", replay.text)
                self.assertIn("event: done", replay.text)


if __name__ == "__main__":
    unittest.main()

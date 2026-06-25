import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Coroutine, Dict, List, Optional


TERMINAL_JOB_STATUSES = {"complete", "error", "stopped"}
JOB_RETENTION_SECONDS = 60 * 30


@dataclass
class ChatJobEvent:
    id: int
    event: str
    data: Dict[str, Any]


@dataclass
class ChatJob:
    conversation_id: str
    agent_id: str
    model: str
    user_message_id: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"
    phase: str = "starting"
    part: int = 1
    tool_step: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    events: List[ChatJobEvent] = field(default_factory=list)
    final_message: Optional[Dict[str, Any]] = None
    error: str = ""
    stop_requested: bool = False
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition, repr=False)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES

    async def publish(self, event: str, data: Dict[str, Any]) -> ChatJobEvent:
        async with self.condition:
            item = ChatJobEvent(len(self.events) + 1, event, data)
            self.events.append(item)
            self.updated_at = time.time()
            self.condition.notify_all()
        return item

    async def set_terminal(
        self,
        status: str,
        *,
        final_message: Optional[Dict[str, Any]] = None,
        error: str = "",
    ) -> None:
        async with self.condition:
            self.status = status
            self.phase = status
            self.final_message = final_message
            self.error = error
            self.updated_at = time.time()
            self.condition.notify_all()

    async def set_work_state(
        self,
        phase: str,
        label: str,
        detail: str = "",
        *,
        part: Optional[int] = None,
        tool_step: Optional[int] = None,
    ) -> None:
        self.phase = phase
        if part is not None:
            self.part = part
        if tool_step is not None:
            self.tool_step = tool_step
        await self.publish(
            "work_state",
            {
                "phase": phase,
                "label": label,
                "detail": detail,
                "part": self.part,
                "tool_step": self.tool_step,
            },
        )

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "agent_id": self.agent_id,
            "model": self.model,
            "user_message_id": self.user_message_id,
            "status": self.status,
            "phase": self.phase,
            "part": self.part,
            "tool_step": self.tool_step,
            "last_event_id": len(self.events),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "final_message": self.final_message,
            "error": self.error,
        }

    async def event_stream(self, after: int = 0) -> AsyncGenerator[str, None]:
        cursor = max(0, after)
        while True:
            pending: List[ChatJobEvent] = []
            terminal = False
            async with self.condition:
                pending = [event for event in self.events if event.id > cursor]
                terminal = self.terminal
                if not pending and not terminal:
                    try:
                        await asyncio.wait_for(self.condition.wait(), timeout=15)
                    except asyncio.TimeoutError:
                        pass
                    pending = [event for event in self.events if event.id > cursor]
                    terminal = self.terminal

            if not pending:
                if terminal:
                    return
                yield ": keep-alive\n\n"
                continue

            for item in pending:
                cursor = item.id
                data = json.dumps(item.data, ensure_ascii=False)
                yield f"id: {item.id}\nevent: {item.event}\ndata: {data}\n\n"

            if terminal and cursor >= len(self.events):
                return


class ChatJobManager:
    def __init__(self) -> None:
        self.jobs: Dict[str, ChatJob] = {}

    def purge(self) -> None:
        cutoff = time.time() - JOB_RETENTION_SECONDS
        stale_ids = [job_id for job_id, job in self.jobs.items() if job.terminal and job.updated_at < cutoff]
        for job_id in stale_ids:
            self.jobs.pop(job_id, None)

    def create(self, conversation_id: str, agent_id: str, model: str, user_message_id: str = "") -> ChatJob:
        self.purge()
        job = ChatJob(
            conversation_id=conversation_id,
            agent_id=agent_id,
            model=model,
            user_message_id=user_message_id,
        )
        self.jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[ChatJob]:
        self.purge()
        return self.jobs.get(job_id)

    def active(self) -> List[ChatJob]:
        self.purge()
        return sorted(
            (job for job in self.jobs.values() if not job.terminal),
            key=lambda job: job.created_at,
            reverse=True,
        )

    def start(self, job: ChatJob, coroutine: Coroutine[Any, Any, None]) -> None:
        job.task = asyncio.create_task(coroutine, name=f"sammy-chat-{job.id}")

    async def stop(self, job: ChatJob) -> None:
        if job.terminal or job.stop_requested:
            return
        job.stop_requested = True
        await job.set_work_state(
            "stopping",
            "Stopping",
            "Waiting for the current tool call to return." if job.phase == "tool" else "Stopping generation.",
        )
        if job.task and job.phase != "tool":
            job.task.cancel()


chat_job_manager = ChatJobManager()

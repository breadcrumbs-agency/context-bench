"""codex-sessions: agent sessions with tool calls and tool results (the OpenAI chat format), one chat for each run.

There are no questions. The replay sends each run through an engine at the points where the agent called the model:
at the user message, and after the last tool result of each run of tool results. It moves the cuts of the engine and
not the calls of the model, so it measures exposure and cost, not behavior. The chat ids are row numbers, not the ids
of the sessions."""

import json

from contextbench.chats import SYSTEM, Chat, agent_requests

FILE = "trajectories.train.jsonl"


def chats(files, limit=0):
    with open(files[FILE], encoding="utf-8") as f:
        for k, line in enumerate(f):
            if limit and k >= limit:
                break
            msgs = json.loads(line)["messages"]
            if not msgs or msgs[0].get("role") != "system":
                msgs = [dict(SYSTEM)] + msgs
            yield Chat("row-%03d" % k, msgs, agent_requests(msgs))

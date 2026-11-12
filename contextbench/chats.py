"""Chats: what a source gives to the stream.

A source yields Chat objects. A chat is a list of messages (the first is the system message) and the points at
which the chat calls the model. Where the source has questions, each question names its gold turns by message
index. The engine never gets a Question: only the text of the question, after the last request of the chat.
"""

import dataclasses

SYSTEM = {"role": "system", "content": "You are a helpful assistant."}


@dataclasses.dataclass
class Question:
    id: str
    text: str
    category: object  # shown as text on the board
    answer: object  # the gold answer text, or None when the answer is not scored
    gold: list  # the message indexes of the gold turns (one index may repeat); never given to an engine


@dataclasses.dataclass
class Chat:
    id: str
    messages: list  # the system message first, then the turns in order
    requests: list  # the message indexes i at which the chat calls the model: the request is messages[:i + 1]
    questions: list = dataclasses.field(default_factory=list)


def chat_of(conv):
    """(messages, dia -> message index): one message per turn; the first speaker is the user. Port of ace_bench."""
    msgs, where, first, last_sess = [dict(SYSTEM)], {}, None, None
    for t in conv["turns"]:
        role = t.get("role")
        if role is None:
            speaker = t["body"].split(":", 1)[0]
            first = first or speaker
            role = "user" if speaker == first else "assistant"
        body = t["body"] if t["sess"] == last_sess else "[%s] %s" % (t["date"], t["body"])
        last_sess = t["sess"]
        where[t["dia"]] = len(msgs)
        msgs.append({"role": role, "content": body})
    return msgs, where


def dialogue_requests(msgs):
    """A chat of people calls the model at each user message, as Hermes sends them."""
    return [i for i in range(1, len(msgs)) if msgs[i]["role"] == "user"]


def agent_requests(msgs):
    """An agent calls the model at each user message and after the last tool result of each run of tool results."""
    out = []
    for i in range(1, len(msgs)):
        role = msgs[i].get("role")
        if role == "user" or (role == "tool" and (i + 1 == len(msgs) or msgs[i + 1].get("role") != "tool")):
            out.append(i)
    return out

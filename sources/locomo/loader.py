"""locomo: ten long conversations between two people, each one chat. A port of ace_bench's loader (locomo.load and
chat_of): one request at each user message; each question is the new user message at the end of its chat."""

import json
import re

from contextbench.chats import Chat, Question, chat_of, dialogue_requests

FILE = "locomo10.json"


def chats(files, limit=0):
    """The conversations in dataset order (the first `limit` when limit is set). A question keeps the evidence ids
    that name a turn; a question with none is out. Category 5 (adversarial) has no answer to score."""
    with open(files[FILE], encoding="utf-8") as f:
        raw = json.load(f)
    for k, s in enumerate(raw):
        if limit and k >= limit:
            break
        c = s["conversation"]
        turns = []
        for key in sorted((key for key in c if re.fullmatch(r"session_\d+", key)), key=lambda key: int(key.split("_")[1])):
            date = c.get(key + "_date_time", "")
            for t in c[key]:
                body = "%s: %s" % (t["speaker"], t["text"])
                if t.get("blip_caption"):
                    body += " [shared image: %s]" % t["blip_caption"]
                turns.append({"dia": t["dia_id"], "sess": key, "date": date, "body": body, "speaker": t["speaker"]})
        known = {t["dia"] for t in turns}
        msgs, where = chat_of({"turns": turns})
        questions = []
        for i, q in enumerate(s["qa"]):
            gold = sorted({p for e in q.get("evidence") or [] for p in re.split(r"[;,\s]+", e.strip()) if p in known})
            if gold:
                cat = q["category"]
                answer = None if cat == 5 else q.get("answer", "")
                questions.append(Question("%s/%d" % (s["sample_id"], i), q["question"], cat, answer,
                                          [where[d] for d in gold]))
        yield Chat(s["sample_id"], msgs, dialogue_requests(msgs), questions)

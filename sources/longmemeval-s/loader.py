"""longmemeval-s: a user and an assistant, one haystack of sessions for each question, so one chat for each question.
A port of ace_bench's load_lme and chat_of: one request at each user message, the sessions in the order that the file
gives (not sorted by date: the numbers must compare with the ace_bench runs of Nov 9 2026)."""

import json

from contextbench.chats import Chat, Question, chat_of, dialogue_requests

FILE = "longmemeval_s_cleaned.json"


def chats(files, limit=0):
    """The questions that ace_bench's --lme N kept: the non-abstention questions, by an even stride when limit is
    set, and only those with a gold turn: a user turn with has_answer in an answer session. Without a limit, N is
    the number of questions (the stride is 1): 470 questions, 419 of them with a gold turn."""
    with open(files[FILE], encoding="utf-8") as f:
        data = [x for x in json.load(f) if "_abs" not in x["question_id"]]
    n = limit or len(data)
    for x in data[::max(1, len(data) // n)][:n]:
        turns, gold = [], []
        for sid, sess, date in zip(x["haystack_session_ids"], x["haystack_sessions"], x["haystack_dates"]):
            for i, t in enumerate(sess):
                dia = "%s_%d" % (sid, i + 1)
                turns.append({"dia": dia, "body": t["content"], "sess": sid, "date": date, "role": t["role"]})
                if t["role"] == "user" and "answer" in sid and t.get("has_answer"):
                    gold.append(dia)
        if gold:
            msgs, where = chat_of({"turns": turns})
            q = Question(x["question_id"], x["question"], x["question_type"], str(x["answer"]),
                         [where[d] for d in sorted(gold)])
            yield Chat(x["question_id"], msgs, dialogue_requests(msgs), [q])

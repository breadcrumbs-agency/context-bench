import json
import os
import tempfile
import unittest

from contextbench import sources
from support import ROOT

LOCOMO = [{
    "sample_id": "conv-t",
    "conversation": {
        "speaker_a": "Ann", "speaker_b": "Bob",
        "session_1_date_time": "1:00 pm on 1 May, 2023",
        "session_1": [
            {"speaker": "Ann", "dia_id": "D1:1", "text": "I brew green tea at 80 C every morning."},
            {"speaker": "Bob", "dia_id": "D1:2", "text": "My bike chain broke on the hill."},
        ],
        "session_10_date_time": "9:00 am on 1 July, 2023",
        "session_10": [{"speaker": "Ann", "dia_id": "D10:1", "text": "The kettle is new."}],
        "session_2_date_time": "2:00 pm on 8 May, 2023",
        "session_2": [
            {"speaker": "Ann", "dia_id": "D2:1", "text": "The garden tomatoes are red now.",
             "blip_caption": "a photo of tomatoes"},
            {"speaker": "Bob", "dia_id": "D2:2", "text": "I fixed the bike chain with a new link."},
        ],
    },
    "qa": [
        {"question": "What temperature does Ann brew green tea at?", "answer": "80 C", "evidence": ["D1:1"],
         "category": 1},
        {"question": "How did Bob fix the bike chain?", "answer": "with a new link", "evidence": ["D1:2; D2:2"],
         "category": 4},
        {"question": "What color are the garden tomatoes?", "adversarial_answer": "green", "evidence": ["D2:1"],
         "category": 5},
        {"question": "Who is the queen?", "answer": "nobody", "evidence": [], "category": 2},
        {"question": "Which id is unknown?", "answer": 2022, "evidence": ["D9:9"], "category": 2},
    ],
}, {
    "sample_id": "conv-u",
    "conversation": {"session_1_date_time": "d", "session_1": [{"speaker": "Cy", "dia_id": "D1:1", "text": "hi"}]},
    "qa": [{"question": "Who said hi?", "answer": "Cy", "evidence": ["D1:1"], "category": 1}],
}]


def lme_item(qid, sessions, gold_role="user", answer_session=True):
    """sessions: [(session id, [(role, text)])]; the first turn of the last session is the gold turn."""
    ids, dates, body = [], [], []
    for k, (sid, turns) in enumerate(sessions):
        ids.append(sid)
        dates.append("2023/05/%02d (Sun) 10:00" % (20 - k))  # the dates run backwards: the file order is not time order
        body.append([{"role": r, "content": t} for r, t in turns])
    gold_session = body[-1] if answer_session else body[0]
    gold_session[0]["has_answer"] = True
    return {"question_id": qid, "question_type": "single-session-user", "question": "Question of " + qid + "?",
            "answer": 42, "haystack_session_ids": ids, "haystack_dates": dates, "haystack_sessions": body}


def lme(n, abstain=None, nogold=None):
    out = []
    for i in range(n):
        qid = "q%d" % i + ("_abs" if i == abstain else "")
        sess = [("s_%d" % i, [("user", "hello %d" % i), ("assistant", "hi %d" % i)]),
                ("answer_%d" % i, [("user", "my tea is green %d" % i), ("assistant", "ok %d" % i)])]
        item = lme_item(qid, sess)
        if i == nogold:
            item["haystack_sessions"][1][0].pop("has_answer")  # a question with no gold turn
        out.append(item)
    return out


class Loaders(unittest.TestCase):
    def load(self, name, raw, limit=0, lines=False):
        loader = sources.load_source(os.path.join(ROOT, "sources", name))[1]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "data")
            with open(path, "w") as f:
                if lines:
                    f.write("\n".join(json.dumps(x) for x in raw) + "\n")
                else:
                    json.dump(raw, f)
            return list(loader.chats({loader.FILE: path}, limit))


class TestLocomo(Loaders):
    def test_sessions_in_number_order_one_message_for_each_turn(self):
        a, b = self.load("locomo", LOCOMO)
        self.assertEqual(a.id, "conv-t")
        self.assertEqual([m["role"] for m in a.messages], ["system", "user", "assistant", "user", "assistant", "user"])
        self.assertEqual(a.messages[1]["content"], "[1:00 pm on 1 May, 2023] Ann: I brew green tea at 80 C every morning.")
        self.assertEqual(a.messages[2]["content"], "Bob: My bike chain broke on the hill.")
        self.assertEqual(a.messages[3]["content"], "[2:00 pm on 8 May, 2023] Ann: The garden tomatoes are red now. "
                                                   "[shared image: a photo of tomatoes]")  # session_2 before session_10
        self.assertEqual(a.messages[5]["content"], "[9:00 am on 1 July, 2023] Ann: The kettle is new.")
        self.assertEqual(a.requests, [1, 3, 5])
        self.assertEqual(b.id, "conv-u")

    def test_questions_keep_the_evidence_ids_that_name_a_turn(self):
        a = self.load("locomo", LOCOMO)[0]
        self.assertEqual([q.id for q in a.questions], ["conv-t/0", "conv-t/1", "conv-t/2"])  # no id, unknown id: out
        self.assertEqual([q.gold for q in a.questions], [[1], [2, 4], [3]])  # "D1:2; D2:2" is two ids
        self.assertEqual([q.answer for q in a.questions], ["80 C", "with a new link", None])  # category 5: no answer
        self.assertEqual([q.category for q in a.questions], [1, 4, 5])

    def test_limit_keeps_the_first_conversations(self):
        self.assertEqual([c.id for c in self.load("locomo", LOCOMO, limit=1)], ["conv-t"])


class TestLongMemEval(Loaders):
    def test_the_sessions_go_in_the_file_order_with_the_date_before_each_session(self):
        (chat,) = self.load("longmemeval-s", lme(1))
        self.assertEqual(chat.id, "q0")
        self.assertEqual([m["role"] for m in chat.messages], ["system", "user", "assistant", "user", "assistant"])
        self.assertEqual(chat.messages[1]["content"], "[2023/05/20 (Sun) 10:00] hello 0")
        self.assertEqual(chat.messages[3]["content"], "[2023/05/19 (Sun) 10:00] my tea is green 0")  # a day earlier
        self.assertEqual(chat.requests, [1, 3])
        (q,) = chat.questions
        self.assertEqual((q.id, q.text, q.answer, q.gold), ("q0", "Question of q0?", "42", [3]))

    def test_abstention_and_no_gold_questions_are_out(self):
        chats = self.load("longmemeval-s", lme(5, abstain=1, nogold=3))
        self.assertEqual([c.id for c in chats], ["q0", "q2", "q4"])

    def test_gold_is_a_user_turn_with_has_answer_in_an_answer_session(self):
        other = lme_item("qa", [("s_a", [("user", "one")]), ("answer_a", [("assistant", "two"), ("user", "three")])],
                         answer_session=True)  # the has_answer turn is an assistant turn: no gold
        wrong_session = lme_item("qb", [("s_a", [("user", "one")]), ("s_b", [("user", "two")])], answer_session=True)
        self.assertEqual(self.load("longmemeval-s", [other, wrong_session]), [])

    def test_limit_is_an_even_stride_over_the_non_abstention_questions(self):
        raw = lme(10, abstain=0)  # 9 questions left: q1 .. q9
        self.assertEqual([c.id for c in self.load("longmemeval-s", raw)], ["q%d" % i for i in range(1, 10)])
        self.assertEqual([c.id for c in self.load("longmemeval-s", raw, limit=3)], ["q1", "q4", "q7"])  # stride 3
        self.assertEqual([c.id for c in self.load("longmemeval-s", raw, limit=4)], ["q1", "q3", "q5", "q7"])  # stride 2
        self.assertEqual(len(self.load("longmemeval-s", raw, limit=50)), 9)  # a stride of 1, as --lme 500 was


class TestCodexSessions(Loaders):
    ROWS = [
        {"id": "secret-turn#1", "messages": [
            {"role": "system", "content": "s"}, {"role": "user", "content": "do it"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "shell", "arguments": "{}"}},
                {"id": "c2", "type": "function", "function": {"name": "shell", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "a"}, {"role": "tool", "tool_call_id": "c2", "content": "b"},
            {"role": "assistant", "content": "done"}]},
        {"id": "secret-turn#2", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]},
    ]

    def test_one_chat_for_each_row_with_a_request_at_each_call_of_the_model(self):
        a, b = self.load("codex-sessions", self.ROWS, lines=True)
        self.assertEqual((a.id, b.id), ("row-000", "row-001"))  # row numbers, not the ids of the sessions
        self.assertEqual(a.requests, [1, 4])  # the user message, and the last of the two tool results
        self.assertEqual(a.questions, [])
        self.assertEqual(b.messages[0]["role"], "system")  # a row with no system message gets the shared one
        self.assertEqual(b.requests, [1])

    def test_limit(self):
        self.assertEqual(len(self.load("codex-sessions", self.ROWS, limit=1, lines=True)), 1)


if __name__ == "__main__":
    unittest.main()

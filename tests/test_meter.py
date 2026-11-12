import unittest

from contextbench import meter
from contextbench.chats import agent_requests, chat_of, dialogue_requests
from support import msg

CARD = meter.load_card()


class TestSizes(unittest.TestCase):
    def test_message_text_and_bytes(self):
        self.assertEqual(meter.message_bytes({"role": "user", "content": "abcd"}), 20)  # 4 + 16 of framing
        call = {"function": {"name": "ls", "arguments": "{}"}}
        self.assertEqual(meter.message_text({"role": "assistant", "content": None, "tool_calls": [call]}),
                         "\u2192 ls {}")
        self.assertEqual(meter.message_bytes({"role": "assistant", "content": None, "tool_calls": [call]}), 9 + 16)
        self.assertEqual(meter.message_bytes({"role": "assistant", "content": "hi", "tool_calls": [call]}), 12 + 16)
        self.assertEqual(meter.message_bytes({"role": "assistant", "content": "ab", "reasoning": "think"}), 2 + 5 + 16)
        self.assertEqual(meter.message_text({"role": "user", "content": [{"type": "text", "text": "a"},
                                                                         {"type": "image"}]}), "a\n[image]")

    def test_request_tokens_are_bytes_over_four(self):
        msgs = [msg("system", 4), msg("user", 44)]
        self.assertEqual(meter.request_bytes(msgs), 20 + 60)
        self.assertEqual(meter.tokens(msgs), 20.0)
        self.assertEqual(meter.tokens([msg("user", 1)]), 17 / 4)
        self.assertEqual([meter.text_tokens(s) for s in ("", "a", "abcd", "abcde")], [0, 1, 1, 2])

    def test_sizer_agrees_with_the_plain_count(self):
        msgs = [msg("system", 4), msg("user", 44), msg("assistant", 7)]
        sizer = meter.Sizer()
        self.assertEqual(sizer.request(msgs), meter.request_bytes(msgs))
        self.assertEqual(sizer.request(msgs[:2]), meter.request_bytes(msgs[:2]))

    def test_tail_kept(self):
        newest = msg("user", 10)
        self.assertTrue(meter.tail_kept([msg("system", 4), newest], newest))
        layered = dict(newest, content="layer\n" + newest["content"])  # ACE puts its layer in front of a message
        self.assertTrue(meter.tail_kept([msg("system", 4), layered], newest))
        self.assertFalse(meter.tail_kept([msg("system", 4)], newest))
        self.assertFalse(meter.tail_kept([], newest))


class TestReplay(unittest.TestCase):
    def setUp(self):
        self.sys, self.u1, self.a1, self.u2 = msg("system", 4), msg("user", 44, id=1), msg("assistant", 44), \
            msg("user", 44, id=2)  # 20, 60, 60, 60 bytes

    def test_cache_reuse_is_the_shared_leading_prefix(self):
        r = meter.Replay(meter.Sizer(), CARD, 1000)
        r.add([self.sys, self.u1], self.u1)  # no request before: nothing cached
        self.assertEqual((r.tokens, r.cached), (20.0, 0.0))
        r.add([self.sys, self.u1, self.a1, self.u2], self.u2)  # the first two messages are shared
        self.assertEqual((r.tokens, r.cached), (70.0, 20.0))
        r.add([self.sys, self.a1, self.u2], self.u2)  # a cut: only the system message is still shared
        self.assertEqual((r.tokens, r.cached), (105.0, 25.0))
        self.assertEqual(r.requests, 3)

    def test_cost_in_token_units(self):
        self.assertEqual(meter.CACHE_READ, 0.1)
        self.assertEqual(meter.CACHE_WRITE, 1.25)
        self.assertAlmostEqual(meter.cost_units(50, 20), 0.1 * 20 + 1.25 * 30)
        r = meter.Replay(meter.Sizer(), CARD, None)
        r.add([self.sys, self.u1], self.u1)
        r.add([self.sys, self.u1, self.a1, self.u2], self.u2)
        self.assertAlmostEqual(r.units, 1.25 * 20 + (0.1 * 20 + 1.25 * 30))

    def test_dollars_follow_the_price_card(self):
        r = meter.Replay(meter.Sizer(), CARD, None)
        r.add([self.sys, self.u1], self.u1)
        r.add([self.sys, self.u1, self.a1, self.u2], self.u2)
        self.assertAlmostEqual(r.usd["claude-sonnet-5-5"], (20 * 2.50 + 20 * 0.10 + 30 * 2.50) / 1e6)
        self.assertAlmostEqual(r.usd["claude-haiku-5-5"], (20 * 0.125 + 20 * 0.01 + 30 * 0.125) / 1e6)
        self.assertAlmostEqual(r.usd["claude-opus-5-5"], (20 * 5.00 + 20 * 0.20 + 30 * 5.00) / 1e6)

    def test_price_card_has_a_date_and_a_hash(self):
        self.assertEqual(CARD["date"], "2026-10-07")
        self.assertEqual(len(CARD["sha256"]), 64)

    def test_haiku_price_steps_up_over_100000_tokens(self):
        low = "claude-haiku-5-5"
        self.assertAlmostEqual(meter.dollars(CARD, low, 100000, 0), 100000 * 0.125 / 1e6)  # up to 100,000: inclusive
        self.assertAlmostEqual(meter.dollars(CARD, low, 100000.25, 0), 100000.25 * 0.625 / 1e6)  # all tokens step up
        # cache reads count toward the 100,000, and they are priced in the higher tier too
        self.assertAlmostEqual(meter.dollars(CARD, low, 100000.25, 100000), (100000 * 0.05 + 0.25 * 0.625) / 1e6)
        self.assertAlmostEqual(meter.dollars(CARD, low, 99999, 99000), (99000 * 0.01 + 999 * 0.125) / 1e6)
        self.assertEqual(meter.tier_of(CARD["models"][low], 100000)["cache_write"], 0.125)
        self.assertEqual(meter.tier_of(CARD["models"][low], 100001)["cache_write"], 0.625)

    def test_other_models_have_no_step(self):
        for name, read, write in (("claude-sonnet-5-5", 0.10, 2.50), ("claude-opus-5-5", 0.20, 5.00)):
            self.assertAlmostEqual(meter.dollars(CARD, name, 200000, 50000), (50000 * read + 150000 * write) / 1e6)

    def test_over_the_budget_is_counted_but_not_cut(self):
        r = meter.Replay(meter.Sizer(), CARD, 20)
        r.add([self.sys, self.u1], self.u1)  # 20 tokens: exactly the budget is not over
        self.assertEqual((r.over, r.excess), (0, 0.0))
        r.add([self.sys, self.u1, self.a1], self.a1)  # 35 tokens: 15 over
        r.add([self.sys, self.u1, self.a1, self.u2], self.u2)  # 50 tokens: 30 over
        self.assertEqual((r.over, r.excess, r.top), (2, 45.0, 50.0))

    def test_no_budget_is_never_over(self):
        r = meter.Replay(meter.Sizer(), CARD, None)
        r.add([self.sys, self.u1], self.u1)
        self.assertEqual((r.over, r.excess), (0, 0.0))

    def test_a_request_that_loses_the_newest_message_is_counted(self):
        r = meter.Replay(meter.Sizer(), CARD, 1000)
        r.add([self.sys], self.u1)
        r.add([self.sys, self.u1], self.u1)
        self.assertEqual(r.dropped, 1)


class TestScoring(unittest.TestCase):
    def test_exact_needs_the_full_text_of_each_gold_turn(self):
        msgs = [msg("system", 4), {"role": "user", "content": "[1 May] Ann:  I brew\ngreen tea."},
                {"role": "assistant", "content": "Bob: ok."}, {"role": "user", "content": "Ann: more tea."}]
        ctx = meter.flat("[1 May] Ann: I brew green tea.\nBob: ok.")
        self.assertEqual(meter.needle(msgs[1]["content"]), "Ann: I brew green tea.")  # no date stamp, spaces made equal
        self.assertEqual(meter.exact_share(ctx, msgs, [1]), 1.0)
        self.assertEqual(meter.exact_share(ctx, msgs, [1, 2, 3]), 2 / 3)
        self.assertEqual(meter.exact_share(meter.flat("Ann: I brew green"), msgs, [1]), 0.0)  # part of a turn: no
        self.assertEqual(meter.exact_share(ctx, msgs, [1, 1, 3]), 1 / 3)  # a repeated gold turn counts in the divisor

    def test_the_context_is_every_message_but_the_question(self):
        out = [msg("system", 4), {"role": "user", "content": "one  two"}, {"role": "user", "content": "the question"}]
        self.assertEqual(meter.context_text(out), "xxxx one two")
        self.assertNotIn("question", meter.context_text(out))

    def test_words_share(self):
        self.assertEqual(meter.words_share("green is nice", "What tea does Ann brew?", "green tea 80"), 0.5)
        self.assertIsNone(meter.words_share("anything", "q", None))
        self.assertIsNone(meter.words_share("anything", "tea brew", "the tea brew"))  # the question's own words
        self.assertEqual(meter.words_share("red", "q?", "Red red RED"), 1.0)


class TestChats(unittest.TestCase):
    def turns(self):
        return [{"dia": "D1:1", "sess": "s1", "date": "1 May", "body": "Ann: hi"},
                {"dia": "D1:2", "sess": "s1", "date": "1 May", "body": "Bob: hello"},
                {"dia": "D2:1", "sess": "s2", "date": "8 May", "body": "Ann: back"}]

    def test_chat_of_makes_the_first_speaker_the_user(self):
        msgs, where = chat_of({"turns": self.turns()})
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant", "user"])
        self.assertEqual(msgs[1]["content"], "[1 May] Ann: hi")  # the date opens each session, once
        self.assertEqual(msgs[2]["content"], "Bob: hello")
        self.assertEqual(msgs[3]["content"], "[8 May] Ann: back")
        self.assertEqual(where, {"D1:1": 1, "D1:2": 2, "D2:1": 3})
        self.assertEqual(dialogue_requests(msgs), [1, 3])

    def test_a_turn_with_a_role_keeps_it(self):
        msgs, _ = chat_of({"turns": [{"dia": "a", "sess": "s", "date": "d", "body": "x", "role": "assistant"},
                                     {"dia": "b", "sess": "s", "date": "d", "body": "y", "role": "user"}]})
        self.assertEqual([m["role"] for m in msgs], ["system", "assistant", "user"])

    def test_agent_requests_follow_the_calls_of_the_model(self):
        roles = ["system", "user", "assistant", "tool", "tool", "assistant", "tool", "assistant", "user", "assistant"]
        msgs = [{"role": r, "content": ""} for r in roles]
        # the model is called at the user messages, and after the last tool result of each run of tool results
        self.assertEqual(agent_requests(msgs), [1, 4, 6, 8])
        self.assertEqual(agent_requests(msgs[:4]), [1, 3])  # a run that ends on a tool result


if __name__ == "__main__":
    unittest.main()

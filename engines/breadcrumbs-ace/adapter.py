"""breadcrumbs-ace: ACE, the context engine of the breadcrumbs plugin, driven as ace_bench drives it.

The engine is breadcrumbs.ace.Ace at its default settings. The budget is its target (target_tokens). It runs in a
scratch home for each chat (workdir), with the harness's token counter (0.25 tokens per byte) set by hand, and
select_context() is called with the messages only. A request that ACE leaves alone comes back as None, and then the
request goes out as it is. state() and restore() keep the fold, so each question sees the end-of-chat state.
"""

import contextbench


class Adapter(contextbench.Engine):
    def __init__(self, workdir, manifest, budget):
        super().__init__(workdir, manifest, budget)
        self.require()
        from breadcrumbs import ace
        self.eng = ace.Ace(target_tokens=budget)
        self.eng.on_session_start("bench", hermes_home=workdir)
        self.eng.tpb = self.eng.ratio = 0.25  # the harness counts a token as 4 bytes

    def select(self, request_messages, budget):
        return self.eng.select_context(request_messages) or request_messages

    def state(self):
        e = self.eng
        return dict(levels=dict(e.levels), layer=e.layer, boundary=e.boundary, folds=e.folds,
                    layer_crumbs=e.layer_crumbs, _sent=e._sent)

    def restore(self, state):
        for k, v in state.items():
            setattr(self.eng, k, v)

    def stats(self):
        return {"folds": self.eng.folds}

    def close(self):
        self.eng.close()

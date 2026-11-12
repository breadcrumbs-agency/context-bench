"""full: no limit. The ceiling row: the whole chat goes out at each request."""

import contextbench


class Adapter(contextbench.Engine):
    ceiling = True

    def select(self, request_messages, budget):
        return request_messages

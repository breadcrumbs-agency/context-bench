"""context-bench: one stream, one meter, one board for context engines."""

from contextbench.chats import Chat, Question
from contextbench.engine import Engine

PROTOCOL = "context-bench/1"  # change it with any change to the stream, the meter or the scoring
__version__ = "0.1.0"
__all__ = ["PROTOCOL", "Chat", "Engine", "Question"]

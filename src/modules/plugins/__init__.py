"""Built-in plugin modules: echo, text-summarizer, memory-retriever."""

from src.modules.plugins.echo import EchoModule
from src.modules.plugins.summarizer import SummarizerModule
from src.modules.plugins.memory_retriever import MemoryRetrieverModule

__all__ = ["EchoModule", "SummarizerModule", "MemoryRetrieverModule"]

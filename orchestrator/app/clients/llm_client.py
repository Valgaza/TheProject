from typing import Protocol


class LLMClient(Protocol):
    # A Protocol doesn't contain real code — it's a shape/contract. Any
    # class with an async `generate(prompt) -> str` method automatically
    # "satisfies" this Protocol, without needing to inherit from it. This
    # is what makes the LLM swappable later: whoever writes the real client
    # (backed by Ollama, vLLM, or whatever the LLM teammate builds) just
    # needs to match this one method signature, and everything in main.py
    # that calls llm_client.generate(...) keeps working unchanged.
    async def generate(self, prompt: str) -> str: ...


class StubLLMClient:
    """Placeholder implementation used until the real LLM client exists.
    Doesn't call any model — just echoes the prompt back so the rest of the
    orchestrator (routing, context assembly, the HTTP layer) can be built
    and tested end-to-end without waiting on the LLM workflow.
    """

    async def generate(self, prompt: str) -> str:
        return "[stub-llm — no real LLM wired in yet]\n\n" + prompt


# The rest of the app imports this `llm_client` instance and calls
# `.generate(...)` on it — swapping StubLLMClient() for a real
# implementation here is the only change needed once the real LLM's
# interface is decided.
llm_client: LLMClient = StubLLMClient()

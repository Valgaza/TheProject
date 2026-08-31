import re
from dataclasses import dataclass

# Matches these phrases anywhere in the message, ignoring case, so "Search
# the Internet for X" and "search the internet for x" both match.
WEB_TRIGGER_PHRASES = re.compile(
    r"search the internet|search the web|look (it|this) up online",
    re.IGNORECASE,
)


@dataclass
class RoutingDecision:
    # Just a plain container for "which pipelines should main.py call for
    # this question" — a dataclass auto-generates the boring __init__ etc.
    # for a class that's just holding a few fields.
    use_rag: bool
    use_graph: bool
    use_web: bool


def decide(message: str, use_web_flag: bool = False) -> RoutingDecision:
    # Web search only runs if the frontend explicitly asked for it (the
    # "search the internet" button, sent as use_web_flag), OR the user
    # typed one of the trigger phrases themselves.
    use_web = use_web_flag or bool(WEB_TRIGGER_PHRASES.search(message))

    # v1 policy: always check RAG + graph, since both are cheap, local
    # calls — no smarter "is this question even about a document" check
    # yet. See PROJECT_STATUS_REPORT.md and
    # .github/IMPORTANT_BUSINESS_LOGIC_FLAGS.md for discussion of
    # smarter routing options.
    return RoutingDecision(use_rag=True, use_graph=True, use_web=use_web)

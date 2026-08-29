import os

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Required environment variable {key!r} is not set")
    return value


class Settings:
    def __init__(self) -> None:
        self.neo4j_uri = _require("NEO4J_URI")
        self.neo4j_username = _require("NEO4J_USERNAME")
        self.neo4j_password = _require("NEO4J_PASSWORD")
        self.openai_api_key = _require("OPENAI_API_KEY")

import json
from groq import Groq
from .config import settings
from .models import ExtractionResult, ExtractedEntity, ExtractedRelationship, EntityType

EXTRACTION_PROMPT = """Extract entities and relationships from the following text.

Entity types to recognize: Person, Concept, Technology, Organisation, Task, Other

Return ONLY valid JSON in this exact format, no other text:
{
  "entities": [
    {"name": "entity name", "type": "Person|Concept|Technology|Organisation|Task|Other"}
  ],
  "relationships": [
    {"source": "entity name", "target": "entity name", "label": "short relationship description"}
  ]
}

Rules:
- Entity names should be normalized (consistent casing, no duplicates)
- Relationship labels should be short (2-4 words), human-readable
- Only include relationships between entities that appear in the entities list
- If no entities or relationships found, return empty arrays

Text to analyze:
"""


class Extractor:
    def __init__(self):
        self._client = None

    @property
    def client(self) -> Groq:
        if self._client is None:
            self._client = Groq(api_key=settings.GROQ_API_KEY)
        return self._client

    def extract(self, text: str) -> ExtractionResult:
        """Extract entities and relationships from text using Groq LLM."""
        if not text or not text.strip():
            return ExtractionResult.empty()

        try:
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an entity extraction assistant. Return only valid JSON, no explanations."
                    },
                    {
                        "role": "user",
                        "content": EXTRACTION_PROMPT + text
                    }
                ],
                temperature=0.0,
                max_tokens=2048,
            )

            content = response.choices[0].message.content
            return self._parse_response(content)

        except Exception:
            return ExtractionResult.empty()

    def _parse_response(self, content: str) -> ExtractionResult:
        """Parse LLM response into ExtractionResult, handling malformed JSON."""
        try:
            # Try to extract JSON from the response
            content = content.strip()

            # Handle markdown code blocks
            if content.startswith("```"):
                lines = content.split("\n")
                # Remove first and last lines (code block markers)
                content = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
                content = content.strip()

            data = json.loads(content)

            entities = []
            for e in data.get("entities", []):
                try:
                    entity_type = EntityType(e["type"])
                    entities.append(ExtractedEntity(name=e["name"], type=entity_type))
                except (KeyError, ValueError):
                    continue

            entity_names = {e.name for e in entities}
            relationships = []
            for r in data.get("relationships", []):
                try:
                    if r["source"] in entity_names and r["target"] in entity_names:
                        relationships.append(ExtractedRelationship(
                            source=r["source"],
                            target=r["target"],
                            label=r["label"]
                        ))
                except KeyError:
                    continue

            return ExtractionResult(entities=entities, relationships=relationships)

        except (json.JSONDecodeError, KeyError, TypeError):
            return ExtractionResult.empty()

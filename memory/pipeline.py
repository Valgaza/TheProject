from .db import Neo4jClient
from .extractor import Extractor
from .repository import MemoryRepository
from .models import ExtractionResult


class ExtractionPipeline:
    """Main pipeline: text in, graph data out, persisted to Neo4j."""

    def __init__(self, db: Neo4jClient = None):
        self.db = db or Neo4jClient()
        self.extractor = Extractor()
        self.repository = MemoryRepository(self.db)

    def run(self, text: str, user_id: str, project_id: str) -> dict:
        """
        Extract entities and relationships from text and persist to Neo4j.

        Args:
            text: Raw text to extract from
            user_id: User ID for graph ownership
            project_id: Project ID for subgraph linking

        Returns:
            dict with extraction stats and results
        """
        # Step 1: Extract entities and relationships from text
        extraction = self.extractor.extract(text)

        # Step 2: Persist to Neo4j (idempotent)
        stats = self.repository.persist_extraction(
            user_id=user_id,
            project_id=project_id,
            result=extraction
        )

        return {
            "success": True,
            "extraction": extraction.model_dump(),
            "stats": stats
        }

    def extract_only(self, text: str) -> ExtractionResult:
        """Extract without persisting - useful for testing/preview."""
        return self.extractor.extract(text)

    def close(self):
        """Close database connection."""
        self.db.close()

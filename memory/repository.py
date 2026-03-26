from datetime import datetime
import hashlib
import re
from .models import ExtractionResult


def _generate_entity_id(user_id: str, name: str, entity_type: str) -> str:
    """Generate deterministic entity ID for idempotency."""
    key = f"{user_id}:{name.lower()}:{entity_type}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _sanitize_rel_type(label: str) -> str:
    """Convert relationship label to valid Cypher relationship type."""
    # Remove non-alphanumeric chars, replace spaces with underscores, uppercase
    sanitized = re.sub(r'[^a-zA-Z0-9\s]', '', label)
    sanitized = re.sub(r'\s+', '_', sanitized.strip())
    return sanitized.upper() or "RELATES_TO"


class MemoryRepository:
    def __init__(self, db):
        self.db = db

    def create_user(self, user_id):
        query = """
        MERGE (u:User {id: $user_id})
        RETURN u
        """
        return self.db.execute(query, {"user_id": user_id})

    def create_project(self, user_id, project_id, name):
        query = """
        MERGE (u:User {id: $user_id})
        MERGE (p:Project {id: $project_id})
        SET p.name = $name
        MERGE (u)-[:OWNS]->(p)
        RETURN p
        """
        return self.db.execute(query, {
            "user_id": user_id,
            "project_id": project_id,
            "name": name
        })

    def create_entity(self, user_id, project_id, entity_id, name, type):
        query = """
        MERGE (e:Entity {id: $entity_id})
        SET e.name = $name,
            e.type = $type,
            e.created_at = $created_at

        WITH e
        MATCH (u:User {id: $user_id})
        MATCH (p:Project {id: $project_id})

        MERGE (e)-[:BELONGS_TO_USER]->(u)
        MERGE (e)-[:BELONGS_TO_PROJECT]->(p)

        RETURN e
        """
        return self.db.execute(query, {
            "entity_id": entity_id,
            "name": name,
            "type": type,
            "created_at": datetime.utcnow().isoformat(),
            "user_id": user_id,
            "project_id": project_id
        })

    def create_relationship(self, from_id, to_id, rel_type):
        query = f"""
        MATCH (a:Entity {{id: $from_id}})
        MATCH (b:Entity {{id: $to_id}})
        MERGE (a)-[r:{rel_type}]->(b)
        RETURN r
        """
        return self.db.execute(query, {
            "from_id": from_id,
            "to_id": to_id
        })

    def create_entity_relationship(self, from_id: str, to_id: str, label: str):
        """Create relationship between entities with label stored as property."""
        rel_type = _sanitize_rel_type(label)
        query = f"""
        MATCH (a:Entity {{id: $from_id}})
        MATCH (b:Entity {{id: $to_id}})
        MERGE (a)-[r:{rel_type}]->(b)
        ON CREATE SET r.label = $label
        RETURN r
        """
        return self.db.execute(query, {
            "from_id": from_id,
            "to_id": to_id,
            "label": label
        })

    def persist_extraction(
        self,
        user_id: str,
        project_id: str,
        result: ExtractionResult
    ) -> dict:
        """Persist extraction result to Neo4j. Idempotent - safe to run multiple times."""
        entity_ids = {}

        # Create all entities first
        for entity in result.entities:
            entity_id = _generate_entity_id(user_id, entity.name, entity.type.value)
            entity_ids[entity.name] = entity_id
            self.create_entity(
                user_id=user_id,
                project_id=project_id,
                entity_id=entity_id,
                name=entity.name,
                type=entity.type.value
            )

        # Create all relationships
        for rel in result.relationships:
            from_id = entity_ids.get(rel.source)
            to_id = entity_ids.get(rel.target)
            if from_id and to_id:
                self.create_entity_relationship(from_id, to_id, rel.label)

        return {
            "entities_created": len(result.entities),
            "relationships_created": len(result.relationships)
        }

    def get_user_graph(self, user_id: str) -> dict:
        """Get all nodes and edges for a user's graph."""
        # Get all entities belonging to user
        nodes_query = """
        MATCH (e:Entity)-[:BELONGS_TO_USER]->(u:User {id: $user_id})
        RETURN e.id AS id, e.name AS name, e.type AS type, e.created_at AS created_at
        """
        nodes = self.db.execute(nodes_query, {"user_id": user_id})

        # Get all relationships between user's entities
        edges_query = """
        MATCH (a:Entity)-[:BELONGS_TO_USER]->(u:User {id: $user_id})
        MATCH (b:Entity)-[:BELONGS_TO_USER]->(u)
        MATCH (a)-[r]->(b)
        WHERE type(r) <> 'BELONGS_TO_USER' AND type(r) <> 'BELONGS_TO_PROJECT'
        RETURN a.id AS source, b.id AS target, type(r) AS type, r.label AS label
        """
        edges = self.db.execute(edges_query, {"user_id": user_id})

        return {"nodes": nodes, "edges": edges}

    def get_project_graph(self, user_id: str, project_id: str) -> dict:
        """Get all nodes and edges for a specific project's graph."""
        # Get all entities belonging to project
        nodes_query = """
        MATCH (e:Entity)-[:BELONGS_TO_PROJECT]->(p:Project {id: $project_id})
        MATCH (e)-[:BELONGS_TO_USER]->(u:User {id: $user_id})
        RETURN e.id AS id, e.name AS name, e.type AS type, e.created_at AS created_at
        """
        nodes = self.db.execute(nodes_query, {
            "user_id": user_id,
            "project_id": project_id
        })

        # Get all relationships between project's entities
        edges_query = """
        MATCH (a:Entity)-[:BELONGS_TO_PROJECT]->(p:Project {id: $project_id})
        MATCH (b:Entity)-[:BELONGS_TO_PROJECT]->(p)
        MATCH (a)-[:BELONGS_TO_USER]->(u:User {id: $user_id})
        MATCH (b)-[:BELONGS_TO_USER]->(u)
        MATCH (a)-[r]->(b)
        WHERE type(r) <> 'BELONGS_TO_USER' AND type(r) <> 'BELONGS_TO_PROJECT'
        RETURN a.id AS source, b.id AS target, type(r) AS type, r.label AS label
        """
        edges = self.db.execute(edges_query, {
            "user_id": user_id,
            "project_id": project_id
        })

        return {"nodes": nodes, "edges": edges}
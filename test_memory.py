from memory.db import Neo4jClient
from memory.repository import MemoryRepository

db = Neo4jClient()
repo = MemoryRepository(db)

repo.create_user("user_1")
repo.create_project("user_1", "proj_1", "Test Project")

repo.create_entity("user_1", "proj_1", "ent_1", "Neo4j", "Technology")
repo.create_entity("user_1", "proj_1", "ent_2", "Graph DB", "Concept")

repo.create_relationship("ent_1", "ent_2", "IS_A")

db.close()
from neo4j import GraphDatabase
from .config import settings
from .constraints import ensure_constraints

class Neo4jClient:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        )

        # Ensure constraints on startup
        ensure_constraints(self)

    def close(self):
        self.driver.close()

    def execute(self, query, parameters=None):
        with self.driver.session(database=settings.NEO4J_DATABASE) as session:
            return session.run(query, parameters or {}).data()
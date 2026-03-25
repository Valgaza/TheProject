from pydantic import BaseModel
from datetime import datetime
from typing import Literal
from enum import Enum

class User(BaseModel):
    id: str

class Project(BaseModel):
    id: str
    name: str

class Entity(BaseModel):
    id: str
    name: str
    type: str
    created_at: datetime


class EntityType(str, Enum):
    PERSON = "Person"
    CONCEPT = "Concept"
    TECHNOLOGY = "Technology"
    ORGANISATION = "Organisation"
    TASK = "Task"
    OTHER = "Other"


class ExtractedEntity(BaseModel):
    name: str
    type: EntityType


class ExtractedRelationship(BaseModel):
    source: str
    target: str
    label: str


class ExtractionResult(BaseModel):
    entities: list[ExtractedEntity]
    relationships: list[ExtractedRelationship]

    @classmethod
    def empty(cls) -> "ExtractionResult":
        return cls(entities=[], relationships=[])
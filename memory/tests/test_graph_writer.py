"""The graph written from the gate's triples: one entity per thing."""

from nexus_memory.graph_writer import _variant, norm_name, relation_name


def test_names_are_compared_without_case_possessives_or_articles():
    assert norm_name("Meera's") == norm_name("meera")
    assert norm_name("The Thesis") == norm_name("thesis")


def test_shorter_and_longer_forms_are_one_entity():
    """One run split a person into "Meera" and "Meera Iyer", and a database
    into "Postgres" and "PostgreSQL"."""
    assert _variant("meera", "meera iyer")
    assert _variant("postgres", "postgresql")
    assert not _variant("meera", "kavya iyer")
    assert not _variant("jax", "java")  # too short to call a prefix the same thing
    assert not _variant("GreenLoop's beta", "GreenLoop")  # a part of it, not another name


def test_relations_become_graph_labels():
    assert relation_name("is sister of") == "IS_SISTER_OF"
    assert relation_name("HAS_DEADLINE") == "HAS_DEADLINE"
    assert relation_name("") == "RELATES_TO"

"""Checks in code around the memory filter's model, each from a failure in the
scripted memory scenarios (scripts/memory_scenarios.py, 2026-10-13)."""

from __future__ import annotations

from types import SimpleNamespace

from nexus_core.memory_writer import _plausibly_replaced, _rescope, _verbatim
from nexus_core.write_gate import GateFact, is_hypothetical, replaced_terms, uncovered_words


def known(*texts):
    return [SimpleNamespace(fact=t, id=str(i)) for i, t in enumerate(texts)]


def test_a_wrong_replacement_from_the_model_is_ignored():
    """For "we switched from PyTorch to JAX" the model named the supervisor fact."""
    facts = known("Meera's thesis supervisor is Dr. Rao", "Meera's thesis uses PyTorch")
    kept = _plausibly_replaced(facts, "Actually we switched from PyTorch to JAX.", ["Meera uses JAX"], "Meera")
    assert [f.fact for f in kept] == ["Meera's thesis uses PyTorch"]


def test_words_kept_verbatim_are_never_retired():
    """Retiring "We're using PyTorch and the dataset is CheXpert" lost the dataset."""
    line = 'Meera said: "We\'re using PyTorch and the dataset is CheXpert."'
    assert _verbatim(line, "Meera")
    assert _plausibly_replaced(known(line), "We switched from PyTorch to JAX.", ["Meera uses JAX"], "Meera") == []


def test_switches_name_what_they_replace():
    assert replaced_terms("Actually we switched from PyTorch to JAX.") == ["PyTorch"]
    assert replaced_terms("I no longer use Neo4j") == ["Neo4j"]


def test_imagining_is_not_a_fact():
    assert is_hypothetical("If I had more time I'd learn Rust.")
    assert not is_hypothetical("My deadline is Friday.")


def test_details_the_facts_dropped_are_noticed():
    assert uncovered_words("The startup is called GreenLoop, it's a recycling marketplace.",
                           ["GreenLoop is a startup"]) == ["recycling", "marketplace"]
    assert uncovered_words("I'm vegetarian and allergic to peanuts.",
                           ["Meera is vegetarian.", "Meera is allergic to peanuts."]) == []


class FakeDecisions:
    def __init__(self, label):
        self.label_ = label

    async def decide(self, state, questions):
        return SimpleNamespace(label=lambda q: self.label_)


async def test_work_facts_filed_as_personal_go_back_to_the_project():
    facts = [GateFact("Arjun is Meera's co-founder", "user"),
             GateFact("Meera is using JAX for her thesis", "user"),
             GateFact("Meera dislikes early mornings", "user"),
             GateFact("GreenLoop launches on Monday", "project")]
    out = await _rescope(facts, "GreenLoop", FakeDecisions("user"))
    assert [f.scope for f in out] == ["project", "project", "user", "project"]


async def test_the_decision_model_can_move_a_fact_to_the_project():
    out = await _rescope([GateFact("Meera picked a darker colour scheme", "user")], "Website", FakeDecisions("project"))
    assert out[0].scope == "project"



def test_a_garbled_triple_is_repaired_from_its_own_sentence():
    from nexus_core.write_gate import _repaired

    f = _repaired(GateFact("Kavya is Meera Iyer's sister", "user", "Kavya", "IS_SISTER_OF", "Mea ra Iyer"))
    assert f.object == "Meera Iyer"
    f = _repaired(GateFact("Meera Iyer studies at KJ Somaiya", "user", "Meera Iyer", "STUDIES_AT", "K/Somaiya"))
    assert f.object == "KJ Somaiya"
    f = _repaired(GateFact("The thesis / HAS_DEADLINE / 15 December 2026", "project",
                           "The thesis", "HAS_DEADLINE", "15 December 2026"))
    assert f.statement == "The thesis has deadline 15 December 2026."



def test_a_split_word_is_rejoined_against_the_message():
    from nexus_core.write_gate import heal_words

    msg = "My sister Kavya lives in Bangalore and works as a doctor."
    assert heal_words("Kavy a lives in Bangalore", msg) == "Kavya lives in Bangalore"
    assert heal_words("Kavya is a doctor", msg) == "Kavya is a doctor"  # "is a" left alone

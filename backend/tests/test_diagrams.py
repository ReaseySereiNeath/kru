import json

import pytest

from pipeline import prompts
from pipeline.book_check import BookCheck
from pipeline.diagrams import (
    character_map,
    clean_label,
    collect_lines,
    concept_map,
    add_diagrams,
    plot_timeline,
    pop_section,
    process_flow,
    replace_diagram,
    request_template,
    strip_diagram_sections,
    thin_out,
)
from pipeline.extract import ExtractedBook, Section
from pipeline.llm import LLMClient
from tests.conftest import FakeServer

NOTES = """## Summary
Things happen.

## Relationships
- Anna - aunt of - Tom
- Tom - friend of - Ben
None

## Key events
- 3: Tom leaves home
- ?: Ben finds a map

## Carryover
Tom is on the road."""


# ---------------------------------------------------------------- reading the notes


def test_pop_and_strip_sections():
    rest, body = pop_section(NOTES, "Relationships")
    assert body.startswith("- Anna - aunt of - Tom")
    assert "Relationships" not in rest and "## Key events" in rest
    stripped = strip_diagram_sections(NOTES)
    assert "aunt of" not in stripped and "Tom leaves home" not in stripped
    assert "## Summary" in stripped and "## Carryover" in stripped


def test_collect_lines_keeps_order_and_drops_repeats():
    second = "## Relationships\n- ANNA - aunt of - Tom!\n- Tom - enemy of - Rex\n## Key events\n7: The storm"
    relationships, events = collect_lines([NOTES, second])
    assert relationships == ["Anna - aunt of - Tom", "Tom - friend of - Ben", "Tom - enemy of - Rex"]
    assert events == ["3: Tom leaves home", "?: Ben finds a map", "7: The storm"]


def test_thin_out_keeps_lines_spread_over_the_book():
    lines = [f"line {i} " + "word " * 8 for i in range(100)]  # 10 words each
    kept = thin_out(lines, 200)
    assert 15 <= len(kept) <= 20
    assert kept[0] == lines[0] and kept[-1] == lines[-1]
    assert thin_out(lines[:3], 200) == lines[:3]


def test_clean_label():
    assert clean_label('Mr. "Darcy" [the] proud: man', 10) == "Mr. Darcy the proud man"
    assert clean_label("one two three four five six", 4) == "one two three four"
    assert clean_label("Darcy's letter", 5) == "Darcy's letter"


# ---------------------------------------------------------------- drawing


CHARACTERS = {
    "characters": ["Elizabeth Bennet", "Mr. Darcy", "Lady Catherine", "Jane Bennet", "Nobody"],
    "relationships": [
        {"from": "Lady Catherine", "relation": "aunt of", "to": "Darcy", "type": "family"},
        {"from": "Elizabeth Bennet", "relation": "falls in love with", "to": "Mr. Darcy", "type": "love"},
        {"from": "Elizabeth Bennet", "relation": "sister of", "to": "Jane Bennet", "type": "family"},
        {"from": "Lady Catherine", "relation": "opposes", "to": "Elizabeth Bennet", "type": "rivalry"},
        {"from": "Jane Bennet", "relation": "loves", "to": "Elizabeth Bennet", "type": "love"},  # same pair
        {"from": "Ghost", "relation": "haunts", "to": "Mr. Darcy", "type": "other"},  # not a character
        {"from": "Mr. Darcy", "relation": "is", "to": "Mr. Darcy", "type": "other"},  # loop
    ],
}


def test_character_map():
    block = character_map(CHARACTERS)
    assert block.startswith("```mermaid\ngraph LR\n")
    # "Darcy" was matched to "Mr. Darcy"; arrows keep the direction of the sentence.
    assert '"Lady Catherine"' in block and 'C3 -->|"aunt of"| C2' in block
    assert "Ghost" not in block and "Nobody" not in block  # unknown name; no relationships
    assert block.count("-->") == 4                           # same pair and loop dropped
    assert "linkStyle 0,2 stroke:#0f6e6a" in block          # family = teal
    assert "linkStyle 3 stroke:#8a94a8" in block            # unknown type -> other
    assert "class C1" in block                               # Elizabeth: most relationships
    assert "*Line colours: teal = family, pink = love, grey = other." in block


def test_character_map_needs_three_relationships():
    assert character_map({"characters": ["A", "B"], "relationships": [
        {"from": "A", "relation": "x", "to": "B"}]}) is None
    assert character_map({}) is None


def test_timeline_drops_impossible_chapters():
    block = plot_timeline({"events": [
        {"chapter": 1, "event": "The ball: they meet"},
        {"chapter": 34, "event": "Darcy proposes"},
        {"chapter": 20, "event": "Going backwards"},   # earlier than the last one
        {"chapter": 99, "event": "Beyond the book"},   # the book has 61 chapters
        {"chapter": "x", "event": "Not a number"},
        {"chapter": None, "event": "No chapter given"},
        {"chapter": 61, "event": "The wedding"},
    ]}, chapter_count=61)
    assert block.startswith("```mermaid\ngraph TD\n")
    assert 'E1["Chapter 1. The ball they meet"]' in block  # the colon is gone
    assert 'E2["Chapter 34. Darcy proposes"]' in block
    assert 'E3["Going backwards"]' in block      # impossible chapter numbers are dropped
    assert 'E4["Beyond the book"]' in block
    assert 'E7["Chapter 61. The wedding"]' in block
    assert "E6 --> E7" in block and "class E7 ending" in block
    assert plot_timeline({"events": [{"chapter": 1, "event": "x"}]}, 10) is None


def test_concept_map_and_process():
    concepts = concept_map({"core": "Git basics", "concepts": [
        {"name": "Commits (snapshots)", "details": ["save history"]},
        {"name": "Branches", "details": []},
        {"name": "Remotes", "details": ["push", "pull"]},
    ]})
    assert "root((Git basics))" in concepts and "    Commits snapshots" in concepts
    assert "      pull" in concepts
    flow = process_flow({"steps": ["Edit files", "Stage", "Commit"], "repeats": True})
    assert 'S1["Edit files"]' in flow and "S2 --> S3" in flow and "S3 --> S1" in flow
    assert process_flow({"steps": [], "repeats": False}) is None


# ---------------------------------------------------------------- putting them in the summary

SUMMARY = """## The full story
Text.

## Character map
How they connect:

```mermaid
graph LR
  A --> B
```

## Plot timeline
```mermaid
timeline
  bad
```
"""


def test_replace_diagram():
    new = "```mermaid\ngraph LR\n  X --> Y\n```"
    out = replace_diagram(SUMMARY, ("character map",), new)
    assert "A --> B" not in out and "X --> Y" in out
    assert "How they connect:" in out and "timeline\n  bad" in out  # other text untouched
    assert replace_diagram(SUMMARY, ("concept map",), new) == SUMMARY  # no such section
    no_block = "## Character map\nNo diagram written.\n"
    assert replace_diagram(no_block, ("character map",), new).endswith(new + "\n")


def test_every_request_exists_in_the_prompt_file():
    for name in ["CHARACTER MAP", "PLOT TIMELINE", "CONCEPT MAP", "PROCESS"]:
        text = request_template(name)
        assert prompts.placeholders(text) == {"book_title", "book_type", "chapter_count",
                                              "diagram_notes"}
        assert "===" not in text and "Return ONLY valid JSON" in text


# ---------------------------------------------------------------- the whole step

BOOK = ExtractedBook("Pride", "Austen", "txt", [Section("", ["x"])])
FICTION = BookCheck("fiction", "novel", "beginner", chapters=[f"Ch {i}" for i in range(1, 62)])
TIMELINE = {"events": [{"chapter": 1, "event": "Meeting"}, {"chapter": 34, "event": "Proposal"},
                       {"chapter": 61, "event": "Wedding"}]}


def run_step(cfg, respond, summary=SUMMARY):
    server = FakeServer(respond)
    calls = []
    out = add_diagrams(summary, LLMClient(cfg, client=server, sleep=lambda s: None), BOOK,
                       FICTION, [NOTES], cfg, record=lambda step, reply: calls.append(step))
    return out, server, calls


def test_add_diagrams_for_a_novel(cfg):
    def respond(request):
        prompt = request["messages"][1]["content"]
        return json.dumps(CHARACTERS if "most important characters" in prompt else TIMELINE)

    out, server, calls = run_step(cfg, respond)
    assert calls == ["diagram: character map", "diagram: plot timeline"]
    assert all(r["model"] == "final-model" and "response_format" in r for r in server.requests)
    first = server.user_messages()[0]
    assert "Anna - aunt of - Tom" in first and "3: Tom leaves home" in first
    assert "Number of chapters: 61" in first
    assert 'C3 -->|"aunt of"| C2' in out and 'E2["Chapter 34. Proposal"]' in out
    assert "A --> B" not in out and "timeline\n  bad" not in out


def test_bad_answers_keep_the_models_own_diagram(cfg):
    out, server, calls = run_step(cfg, lambda r: "not json at all")
    assert out == SUMMARY
    assert len(calls) == 4  # two tries per diagram


def test_a_failing_model_keeps_the_models_own_diagram(cfg):
    def down(request):
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError):  # a real bug is not hidden...
        run_step(cfg, down)
    from tests.test_llm import connection_error
    out, _, _ = run_step(cfg, lambda r: (_ for _ in ()).throw(connection_error()))
    assert out == SUMMARY  # ...but a model problem only costs the nicer diagram

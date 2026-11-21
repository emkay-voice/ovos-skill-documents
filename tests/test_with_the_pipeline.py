"""The real reading pipeline, on the same bus: does it hand "read me the
document ..." to this skill, in English and Danish, and leave the rest?"""
import pytest

from test_skill import make_skill, samples

pipeline_module = pytest.importorskip("ovos_common_reading_pipeline_plugin")


@pytest.fixture
def both(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "holiday plans.md").write_text("# Holiday Plans\n\nSkagen.")
    (docs / "ferieplaner.txt").write_text("Skagen i juli.")
    (docs / "board.pdf").write_bytes(samples.pdf(["Met."], title="Board Minutes March"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    skill, bus = make_skill(tmp_path, monkeypatch, {"folders": str(docs)})
    pipeline = pipeline_module.CommonReadingPipeline(bus=bus)
    skill._announce_vocabulary()
    yield pipeline, skill
    pipeline.stop()


def claim(pipeline, utterance, lang):
    from ovos_bus_client.message import Message
    message = Message("recognizer_loop:utterance", {"utterances": [utterance], "lang": lang},
                      {"session": {"session_id": "default", "lang": lang}})
    return (pipeline.match_high([utterance], lang, message)
            or pipeline.match_low([utterance], lang, message))


@pytest.mark.parametrize("utterance, lang", [
    ("read me the document holiday plans", "en-us"),
    ("read me a document", "en-us"),
    ("read my documents", "en-us"),
    ("tell me board minutes march", "en-us"),
    ("læs dokumentet ferieplaner", "da-dk"),
    ("læs et dokument", "da-dk"),
    ("læs mine dokumenter", "da-dk"),
])
def test_claimed(both, utterance, lang):
    pipeline, _ = both
    match = claim(pipeline, utterance, lang)
    assert match is not None, f"{utterance!r} was not claimed"


@pytest.mark.parametrize("utterance, lang", [
    ("read me the weather report", "en-us"),
    ("open the file manager", "en-us"),
    ("what time is it", "en-us"),
    ("hvad er klokken", "da-dk"),
])
def test_left_alone(both, utterance, lang):
    pipeline, _ = both
    assert claim(pipeline, utterance, lang) is None

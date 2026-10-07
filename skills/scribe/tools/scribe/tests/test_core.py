import os
import tempfile

os.environ["SCRIBE_HOME"] = tempfile.mkdtemp()

import numpy as np  # noqa: E402

from scribe import job as J  # noqa: E402
from scribe.audio import SR, pick_samples  # noqa: E402
from scribe.backends import normalize  # noqa: E402
from scribe.export import EXPORTERS, build_doc  # noqa: E402
from scribe.merge import merge  # noqa: E402

DIAR = [{"speaker_id": "SPEAKER_00", "start": 0.0, "end": 10.0},
        {"speaker_id": "SPEAKER_01", "start": 9.0, "end": 20.0}]


def test_merge_splits_segment_at_speaker_change_by_words():
    tr = [{"start": 8.0, "end": 12.0, "text": "はいそうです",
           "words": [{"start": 8.0, "end": 8.8, "word": "はい"}, {"start": 10.5, "end": 12.0, "word": "そうです"}]}]
    m = merge(tr, DIAR)
    assert [(x["speaker_id"], x["text"]) for x in m] == [("SPEAKER_00", "はい"), ("SPEAKER_01", "そうです")]


def test_merge_far_from_any_turn_is_unknown_near_is_assigned():
    m = merge([{"start": 30.0, "end": 31.0, "text": "遠い", "words": []},
               {"start": 20.2, "end": 20.6, "text": "近い", "words": []}], DIAR)
    assert [x["speaker_id"] for x in m] == [None, "SPEAKER_01"]


def test_normalize_relabels_and_drops_bad_spans():
    rows = [{"speaker_id": 7, "start": 5, "end": 6}, {"speaker_id": 3, "start": 1, "end": 2},
            {"speaker_id": 3, "start": 4, "end": 4}]
    assert normalize("diarize", rows) == [{"speaker_id": "SPEAKER_00", "start": 1.0, "end": 2.0},
                                          {"speaker_id": "SPEAKER_01", "start": 5.0, "end": 6.0}]


def test_pick_samples_avoids_overlap_and_silence():
    x = np.zeros(SR * 20, dtype=np.float32)
    x[: SR * 9] = 0.1 * np.sin(np.arange(SR * 9) / 5)  # SPEAKER_00 loud only before overlap
    x[SR * 10:] = 0.1 * np.sin(np.arange(SR * 10) / 5)
    spans = pick_samples(x, DIAR)
    assert spans["SPEAKER_00"] == (0.0, 9.0)
    assert spans["SPEAKER_01"] == (10.0, 20.0)


def test_exporters():
    doc = build_doc("j1", 20.0, [{"speaker_id": "SPEAKER_00", "start": 120.4, "end": 127.8, "text": "始めます。"},
                                 {"speaker_id": None, "start": 128.0, "end": 129.0, "text": "はい"}],
                    {"SPEAKER_00": "田中"})
    assert "## 00:02:00 — 田中\n\n始めます。" in EXPORTERS["markdown"](doc)
    assert "00:02:00,400 --> 00:02:07,800\n田中: 始めます。" in EXPORTERS["srt"](doc)
    assert "[00:02:08] UNKNOWN: はい" in EXPORTERS["txt"](doc)
    vtt = EXPORTERS["vtt"](build_doc("j1", 1.0, [{"speaker_id": "SPEAKER_00", "start": 1.5, "end": 2.0,
                                                  "text": "A<B & C"}], {"SPEAKER_00": "田中"}))
    assert vtt == "WEBVTT\n\n00:00:01.500 --> 00:00:02.000\n<v 田中>A&lt;B &amp; C\n"


def test_status_derivation_and_lock():
    job = J.create(__file__, "t1")
    assert J.status(job) == "created"
    d = J.job_dir("t1")
    J.write_json(d / "diarization.json", DIAR)
    job["stages"] = {s: {"status": "completed"} for s in J.STAGES}
    assert J.status(job) == "speaker_identification_required"
    J.write_json(d / "speakers.json", {"SPEAKER_00": "田中", "SPEAKER_01": "佐藤"})
    assert J.status(job) == "ready"
    job["exports"] = {"markdown": {}}
    assert J.status(job) == "exported"
    job["stages"]["merge"]["status"] = "running"  # crashed process: no lock holder
    assert J.status(job) == "failed"
    with J.lock("t1"):
        assert J.is_locked("t1")
    assert not J.is_locked("t1")
    from scribe.cli import cmd_jobs
    assert [j["job_id"] for j in cmd_jobs(None)[0]["jobs"]] == ["t1"]


def test_rename_refreshes_exported_files():
    import argparse

    import pytest

    from scribe.cli import cmd_export, cmd_speaker_rename

    job = J.create(__file__, "t2")
    d = J.job_dir("t2")
    J.write_json(d / "diarization.json", DIAR)
    J.write_json(d / "merged.json", [{"speaker_id": "SPEAKER_00", "start": 0.0, "end": 1.0, "text": "こんにちは"}])
    job["stages"] = {s: {"status": "completed"} for s in J.STAGES}
    J.save(job)
    out = d / "out.vtt"
    cmd_export(argparse.Namespace(job="t2", format="vtt", output=str(out)))
    assert "<v SPEAKER_00>" in out.read_text(encoding="utf-8")
    res, code = cmd_speaker_rename(argparse.Namespace(job="t2", pairs=["SPEAKER_00=田中", "SPEAKER_01=佐藤"]))
    assert code == 0 and res["status"] == "exported" and res["refreshed"] == [str(out.resolve())]
    assert "<v 田中>こんにちは" in out.read_text(encoding="utf-8")
    with pytest.raises(J.ScribeError):
        cmd_speaker_rename(argparse.Namespace(job="t2", pairs=["SPEAKER_00"]))

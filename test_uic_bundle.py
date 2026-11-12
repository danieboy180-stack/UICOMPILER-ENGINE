"""uic.bundle / uic.seal: byte-exact packaging, packager rules, and the seal that makes LLM bundles ingestible."""
import copy
import hashlib
import json

import pytest

import ingest
import ingest_layer342
from uic import bundle, check, fixtures as fx, seal


def test_canonical_hash_matches_both_ingest_scripts():
    v = {"b": [1, {"z": "é"}], "a": None}
    assert bundle.canonical_json(v) == ingest._canonical_json(v) == ingest_layer342._canonical_json(v)
    assert bundle.chash(v) == ingest._hash(v) == ingest_layer342._hash(v)


def test_pack_is_byte_exact_including_crlf_trailing_space_and_unicode(tmp_path):
    s = fx.build_sample()
    text = fx.text12(s).replace("\n", "\r\n").replace('"Sign in"', '"Sign in \u2192  "') + "   \r\n\r\n"
    data = text.encode("utf-8")
    rep = bundle.pack("12", data, tmp_path)
    assert rep.ok, rep.summary()
    assert (tmp_path / "layer12_bundle.txt").read_bytes() == data
    assert rep.sha256 == hashlib.sha256(data).hexdigest()


def test_pack_refuses_garbage_nonutf8_and_incomplete_pair(tmp_path):
    assert not bundle.pack("12", b"hello world", tmp_path).ok
    assert not bundle.pack("12", b"\xff\xfe<LAYER1_OUTPUT>{}</LAYER1_OUTPUT>", tmp_path).ok
    only_l1 = "<LAYER1_OUTPUT>\n{}\n</LAYER1_OUTPUT>"
    rep = bundle.pack("12", only_l1.encode(), tmp_path)
    assert not rep.ok and "incomplete pair" in rep.errors[0]
    assert bundle.pack("12", only_l1.encode(), tmp_path, allow_partial=True).ok
    assert not list(tmp_path.glob("*.tmp"))


def test_pack_342_requires_report_and_manifest_and_flags_inconsistent_bundles(tmp_path):
    s = fx.build_sample()
    no_report = fx.text342(s).split("<REPORT>")[0]
    rep = bundle.validate_pack("342", no_report.encode())
    assert not rep.ok and any("REPORT" in e for e in rep.errors)
    bad = copy.deepcopy(s)
    bad["report342"]["layers"]["layer3"]["status"] = "fail"
    rep = bundle.validate_pack("342", fx.text342(bad).encode())
    assert not rep.ok and any("Layer 4.1 present but Layer 3" in e for e in rep.errors)


def test_pack_342_accepts_a_legitimately_halted_layer3_only_bundle(tmp_path):
    s = fx.build_sample()
    halted = fx._t("LAYER3_OUTPUT", {"validation_failed": True, "halt_reason": "L1 not ready"}) \
        + fx._t("MANIFEST_UPDATE", s["manifest342"]) \
        + fx._t("REPORT", {"layers": {"layer3": {"status": "fail"}}, "halt_reason": "L1 not ready",
                           "halt_stage": "Layer3.Stage0", "blocking_object": None})
    rep = bundle.pack("342", halted.encode(), tmp_path)
    assert rep.ok and rep.halt["halt_stage"] == "Layer3.Stage0"


def test_unsealed_llm_hashes_are_rejected_by_ingest_but_sealed_bundle_is_accepted(tmp_path):
    s = fx.build_sample()
    src = hashlib.sha256(b"img").hexdigest()
    sealed12, _ = seal.seal12(fx.text12(s), src, "proj-a")
    (tmp_path / "Python_Inbox").mkdir()
    (tmp_path / "Python_Inbox" / "layer12_bundle.txt").write_text(sealed12, encoding="utf-8")
    code, run_id, outcome = ingest.ingest_bundle(tmp_path)
    assert (code, outcome) == (0, "ingested")
    (tmp_path / "Python_Inbox" / "layer342_bundle.txt").write_text(fx.text342(s), encoding="utf-8")
    assert ingest_layer342.ingest(tmp_path) == 10  # fabricated hashes: the loophole this layer closes
    sealed342, rep = seal.seal342(fx.text342(s), seal.l2_hash_from_disk(tmp_path), run_id)
    assert any(c["changed"] for c in rep["changes"])
    (tmp_path / "Python_Inbox" / "layer342_bundle.txt").write_text(sealed342, encoding="utf-8")
    assert ingest_layer342.ingest(tmp_path) == 0


def test_seal12_puts_real_provenance_where_ingest_reads_it(tmp_path):
    s = fx.build_sample()
    src = hashlib.sha256(b"real screenshot").hexdigest()
    sealed, rep = seal.seal12(fx.text12(s), src, "stackra-web")
    (tmp_path / "Python_Inbox").mkdir()
    (tmp_path / "Python_Inbox" / "layer12_bundle.txt").write_text(sealed, encoding="utf-8")
    code, run_id, _ = ingest.ingest_bundle(tmp_path)
    manifest = json.loads((tmp_path / "Manifest" / f"manifest_{run_id}.json").read_text())
    assert manifest["image_hash"] == src != "default_image_hash"
    assert manifest["layer1_hash"] == bundle.chash(json.loads((tmp_path / "Artifacts" / "layer1_ccir.json").read_text()))
    assert next(c for c in rep["changes"] if c["field"] == "layer1.image_hash")["declared"] == fx.FAKE


def test_sealed_chains_verify_and_unsealed_chains_do_not():
    s = fx.build_sample()
    l2_hash = bundle.chash(s["l2"])
    bodies = {"L3": s["l3"], "L41": s["l41"], "L42": s["l42"]}
    assert check.has_errors(check.verify_chains(bodies, l2_hash))
    sealed, _ = seal.seal342(fx.text342(s), l2_hash, "run-x")
    tags = bundle.parse_tags(sealed, ("LAYER3_OUTPUT", "LAYER4_1_OUTPUT", "LAYER4_2_OUTPUT"))
    got = {k: json.loads(tags[t]) for k, t in (("L3", "LAYER3_OUTPUT"), ("L41", "LAYER4_1_OUTPUT"), ("L42", "LAYER4_2_OUTPUT"))}
    assert check.verify_chains(got, l2_hash) == []


def test_seal342_is_idempotent_and_never_invents_a_chain_for_a_failed_layer():
    s = fx.build_sample()
    h = bundle.chash(s["l2"])
    once, _ = seal.seal342(fx.text342(s), h, "r")
    twice, rep2 = seal.seal342(once, h, "r")
    t1 = bundle.parse_tags(once, ("LAYER3_OUTPUT", "LAYER4_2_OUTPUT"))
    t2 = bundle.parse_tags(twice, ("LAYER3_OUTPUT", "LAYER4_2_OUTPUT"))
    assert json.loads(t1["LAYER3_OUTPUT"]) == json.loads(t2["LAYER3_OUTPUT"])
    assert json.loads(t1["LAYER4_2_OUTPUT"]) == json.loads(t2["LAYER4_2_OUTPUT"])
    assert not [c for c in rep2["changes"] if c["changed"] and ".chain." in c["field"]]
    failed = fx._t("LAYER3_OUTPUT", {"VALIDATION_GATE": {"gate_result": "fail", "halt_reason": "x"}}) \
        + fx._t("MANIFEST_UPDATE", s["manifest342"]) \
        + fx._t("REPORT", {"layers": {"layer3": {"status": "fail"}}, "halt_reason": "x"})
    out, _ = seal.seal342(failed, h, "r")
    body = json.loads(bundle.parse_tags(out, ("LAYER3_OUTPUT",))["LAYER3_OUTPUT"])
    assert "DESIGN_HASH" not in body and "HASH_CHAIN" not in body


@pytest.mark.parametrize("bad", ["not json", "{", "[1,2]"])
def test_seal_rejects_unparseable_bodies(bad):
    with pytest.raises(ValueError) as exc:
        seal.seal12(f"<LAYER1_OUTPUT>{bad}</LAYER1_OUTPUT><LAYER2_OUTPUT>{{}}</LAYER2_OUTPUT>", "a" * 64, "p")
    assert "cannot seal" in str(exc.value)

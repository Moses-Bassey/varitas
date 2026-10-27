from __future__ import annotations

from app.schemas import Claim, Evidence, TextSpan
from app.services.assessment import assess_claim
from app.services.bias_analysis import detect_bias_locally
from app.services.claims import extract_claims_locally
from app.services.evidence import keywords
from app.services.zetaris import normalize_row


def _claim(text="The plant cut output by 20%.", verifiable=True) -> Claim:
    return Claim(id="c1", text=text, claim_type="statistical", verifiable=verifiable, span=TextSpan(start=0, end=len(text), text=text), initial_uncertainty="high", extracted_by="t")


def _evidence(eid, relation, kind="primary", source=None) -> Evidence:
    return Evidence(id=eid, title=eid, source_id=source or eid, retrieved_at="now", excerpt="x", claim_ids=["c1"], relationships={"c1": relation}, retrieval_method="t", source_kind=kind)


def test_claim_types_and_verifiability():
    claims = extract_claims_locally("Prices rose 12% in 2024. You should act now. The mayor said the plan failed.")
    assert [c.claim_type for c in claims] == ["statistical", "opinion", "attribution"]
    assert [c.verifiable for c in claims] == [True, False, True]


def test_empty_content_yields_no_claims():
    assert extract_claims_locally("   ") == []


def test_neutral_text_has_no_bias_indicators():
    content = "The council approved the budget on Tuesday. The report lists spending by department."
    assert detect_bias_locally(content, extract_claims_locally(content)) == []


def test_bias_detection_examples():
    content = "Either we ban it or the town dies. Experts say it is a catastrophe. Everyone knows the truth."
    techniques = {b.technique for b in detect_bias_locally(content, extract_claims_locally(content))}
    assert {"false_dilemma", "appeal_to_authority", "loaded_language", "overgeneralization"} <= techniques


def test_bias_indicators_carry_caution():
    indicators = detect_bias_locally("This is a DISASTER.", [])
    assert indicators and all("not proof" in i.caution for i in indicators)


def test_verdict_rules():
    claim = _claim()
    assert assess_claim(claim, []).verdict == "unverified"
    assert assess_claim(claim, [_evidence("e1", "supports"), _evidence("e2", "contradicts")]).verdict == "mixed_evidence"
    assert assess_claim(claim, [_evidence("e1", "contextualizes")]).verdict == "insufficient_evidence"
    assert assess_claim(claim, [_evidence("e1", "does_not_establish")]).verdict == "insufficient_evidence"
    strong = assess_claim(claim, [_evidence("e1", "supports"), _evidence("e2", "supports", kind="secondary")])
    assert (strong.verdict, strong.uncertainty) == ("supported", "low")
    weak = assess_claim(claim, [_evidence("e1", "contradicts", kind="secondary")])
    assert (weak.verdict, weak.uncertainty) == ("contradicted", "high")
    assert "No primary source directly addresses this claim." in weak.evidence_gaps
    assert assess_claim(_claim(verifiable=False), [_evidence("e1", "supports")]).verdict == "unverified"


def test_keywords_are_sql_safe():
    terms = keywords("Robert'); DROP TABLE x;-- 50% OF bridges' collapse!")
    assert all(t.isalnum() and t.islower() for t in terms)


def test_zetaris_row_normalization():
    row = ["s1", "Title", "javascript:alert(1)", "Pub", "2025-01-01", "primary", "Body text"]
    assert normalize_row(row)["url"] == ""
    assert normalize_row(["too", "short"]) is None
    assert normalize_row({"source_id": "s2", "content": "c"})["title"] == ""
    assert normalize_row(["", "t", "", "", "", "", "c"]) is None

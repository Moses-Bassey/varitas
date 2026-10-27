"""VERITAS dashboard. Visual shell only: every figure comes from the API response.

Run: streamlit run app/ui/dashboard.py
"""

from __future__ import annotations

import html
import json
import os
import sys
from pathlib import Path

import httpx
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.config import ROOT, apply_env_file

apply_env_file(ROOT / ".env")
API_URL = os.environ.get("VERITAS_API_URL", "http://localhost:8000").rstrip("/")

PAGES = [
    ("dashboard", "Dashboard"),
    ("claims", "Claims"),
    ("evidence", "Evidence"),
    ("media", "Media Analysis"),
    ("bias", "Bias Detection"),
    ("networks", "Networks"),
    ("reports", "Reports"),
]
UNCERTAINTY_WIDTH = {"low": 28, "moderate": 50, "high": 72, "very_high": 90}
SMALL_WORDS = {"to", "or", "of", "vs", "and"}

st.set_page_config(page_title="VERITAS", layout="wide", initial_sidebar_state="expanded")
st.markdown(
    """
<style>
:root { color-scheme: dark; }
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
  background: #07090d; color: #e6e8ee;
}
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { display: none; }
[data-testid="stSidebar"] {
  background: #0b0e14; border-right: 1px solid #1b2230;
}
[data-testid="stSidebar"] > div:first-child { padding-top: 1.1rem; }
.block-container { max-width: 1320px; padding: 1.15rem 1.5rem 2.4rem; }
[data-testid="stSidebar"] label, [data-testid="stSidebar"] p, [data-testid="stSidebar"] span { color: #9aa3b2; }
[data-testid="stSidebar"] input, [data-testid="stSidebar"] textarea {
  background: #10151d !important; color: #e6e8ee !important; border: 1px solid #243044 !important; border-radius: 8px;
}
[data-testid="stTextAreaRootElement"], [data-testid="stTextInputRootElement"] {
  background-color: #0c1118 !important; color: #e6e8ee !important; border: 1px solid #243044 !important; border-radius: 10px;
}
[data-testid="stMain"] textarea, [data-testid="stMain"] input {
  color: #e6e8ee !important; -webkit-text-fill-color: #e6e8ee; background: transparent !important;
}
textarea::placeholder, input::placeholder { color: #8b93a1 !important; }
.st-key-config_panel {
  background: linear-gradient(180deg, #141b26 0%, #10151c 100%);
  border: 1px solid #2a3344; border-radius: 16px; padding: 16px 18px 8px; margin: 0 0 18px;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.28);
}
[data-testid="stMain"] [data-testid="stWidgetLabel"] p { color: #c5cad3 !important; }
[data-testid="stMain"] .stButton > button {
  background: #d7262d; color: #fff; border: none; border-radius: 8px; font-weight: 600;
}
[data-testid="stMain"] .stButton > button:hover { background: #ef3b42; color: #fff; border: none; }
a.nav {
  display: flex; align-items: center; gap: 10px; padding: 9px 12px; margin: 2px 0;
  border-radius: 8px; color: #b7bec8; text-decoration: none; font-size: 0.95rem;
}
[data-testid="stSidebar"] [role="radiogroup"] label { padding: 7px 10px; border-radius: 8px; }
[data-testid="stSidebar"] [role="radiogroup"] label:hover { background: #141a24; }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) { background: #171e2a; }
[data-testid="stSidebar"] [role="radiogroup"] label p { color: #c5cad3 !important; }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p { color: #fff !important; }
[data-testid="stSidebar"] [role="radiogroup"] label > div:first-child { display: none; }
.brand { color: #f2f4f8; font-weight: 750; letter-spacing: 0.16em; font-size: 1.05rem; margin: 0 0 18px 8px; }
.casehead { display: flex; align-items: baseline; gap: 16px; margin: 0 0 18px; }
.inv { color: #e23b3b; font-size: 0.78rem; letter-spacing: 0.14em; font-weight: 700; }
.case { color: #f4f6fa; font-size: 1.15rem; font-weight: 650; }
.demo { color: #c9a3a3; font-size: 0.82rem; margin: -10px 0 16px; }
.grid3 { display: grid; grid-template-columns: 1.15fr 1fr 0.9fr; gap: 14px; }
.grid2 { display: grid; grid-template-columns: 1.45fr 0.9fr; gap: 14px; margin-top: 14px; }
.card {
  background: #10151c; border: 1px solid #222b3a; border-radius: 14px; padding: 16px 18px 18px; min-height: 168px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
}
@media (max-width: 1100px) {
  .grid3, .grid2, .split, .map { grid-template-columns: 1fr; }
}
.card h3 {
  color: #e23b3b; font-size: 0.78rem; letter-spacing: 0.12em; font-weight: 700; margin: 0 0 14px;
}
.claim { color: #f2f4f8; font-size: 1.05rem; line-height: 1.45; margin: 0 0 16px; }
.row { display: flex; align-items: center; gap: 10px; }
.tag { color: #8b93a1; font-size: 0.82rem; }
.pill { display: inline-block; background: #2a3140; color: #f2f4f8; border-radius: 8px; padding: 4px 10px; font-size: 0.8rem; }
.detected { color: #d5d8de; margin: 0 0 8px; }
.tech { color: #ff5c5c; margin: 3px 0; }
.media-line { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; padding: 7px 0; border-bottom: 1px solid #1c2433; }
.media-line b { color: #f2f4f8; font-weight: 600; overflow-wrap: anywhere; }
.note { color: #8b93a1; font-size: 0.82rem; margin-top: 10px; }
.map { display: grid; grid-template-columns: 1fr 180px 1fr; gap: 8px; align-items: start; }
.kicker { font-size: 0.72rem; letter-spacing: 0.12em; font-weight: 700; margin-bottom: 8px; }
.green { color: #3ddc97; } .red { color: #ff5a5a; } .teal { color: #2ee6c7; }
.src { background: #0c1118; border: 1px solid #243044; border-radius: 8px; padding: 8px 10px; margin: 6px 0; color: #d7dbe3; font-size: 0.86rem; }
.hub { display: flex; flex-direction: column; align-items: center; }
.node { width: 46px; height: 34px; background: #d7262d; border-radius: 4px; box-shadow: 0 0 22px rgba(215,38,45,.45); }
.stem { width: 2px; height: 18px; background: #2ee6c7; }
.conclusion { border: 1px solid #1f8f86; border-radius: 8px; padding: 10px; text-align: center; color: #d7dbe3; font-size: 0.84rem; }
.bar { height: 8px; background: #1a2030; border-radius: 99px; overflow: hidden; margin: 8px 0 4px; }
.fill { height: 100%; background: linear-gradient(90deg, #7a1d24, #e23b3b); }
.steps { margin: 8px 0 0; padding-left: 18px; color: #d7dbe3; }
.steps li { margin: 5px 0; }
.stack { display: flex; flex-direction: column; gap: 12px; }
.split { display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 12px; }
.quote { color: #ffb4b4; }
.empty { color: #8b93a1; }
[data-testid="stSidebar"] .stButton > button {
  background: transparent; color: #e6e8ee; border: 1px solid #2a3344; border-radius: 8px; width: 100%;
}
[data-testid="stExpander"] { background: #10151c; border: 1px solid #222b3a; border-radius: 12px; }
</style>
""",
    unsafe_allow_html=True,
)


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def pretty(slug: str) -> str:
    words = str(slug).replace("_", " ").split()
    return " ".join(word if word in SMALL_WORDS else word.capitalize() for word in words)


def uncertainty_word(level: str | None) -> str:
    return (level or "high").replace("_", " ").capitalize()


@st.cache_data(ttl=15)
def health() -> dict | None:
    try:
        return httpx.get(f"{API_URL}/health", timeout=5).json()
    except (httpx.HTTPError, ValueError):
        return None


def sources_for(evidence: list[dict], relation: str) -> list[str]:
    titles: list[str] = []
    for record in evidence:
        if relation in record.get("relationships", {}).values() and record["title"] not in titles:
            titles.append(record["title"])
    return titles[:4]


def overall_uncertainty(assessments: list[dict]) -> str:
    ranks = ["low", "moderate", "high", "very_high"]
    present = [a["uncertainty"] for a in assessments if a.get("uncertainty") in ranks]
    if not present:
        return "high"
    return max(present, key=ranks.index)


def short_conclusion(assessments: list[dict]) -> str:
    counts: dict[str, int] = {}
    for item in assessments:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    if not counts:
        return "No checkable claims."
    verdict = max(counts, key=lambda key: counts[key])
    phrases = {
        "supported": "Checked claims are supported by the retrieved evidence.",
        "contradicted": "Key claims are contradicted by the retrieved evidence.",
        "mixed_evidence": "The retrieved evidence points both ways.",
        "insufficient_evidence": "Insufficient evidence for the stated claim.",
        "unverified": "Not verified with the available evidence.",
    }
    return phrases.get(verdict, pretty(verdict))


def card(title: str, body: str) -> str:
    return f'<section class="card"><h3>{esc(title)}</h3>{body}</section>'


def source_list(titles: list[str]) -> str:
    if not titles:
        return '<p class="empty">None retrieved.</p>'
    return "".join(f'<div class="src">{esc(title)}</div>' for title in titles)


def render_dashboard(result: dict) -> None:
    out = result["output"]
    claims = out["claims"]
    assessments = {item["claim_id"]: item for item in out["assessments"]}
    lead = claims[0] if claims else None
    lead_assessment = assessments.get(lead["id"]) if lead else None
    claim_body = (
        f'<p class="claim">{esc(lead["text"])}</p>'
        f'<div class="row"><span class="tag">{esc(lead["id"].replace("-", " ").title())}</span>'
        f'<span class="pill">{esc(uncertainty_word(lead_assessment["uncertainty"] if lead_assessment else None))} uncertainty</span></div>'
        if lead
        else '<p class="empty">No claims were extracted.</p>'
    )
    techniques = []
    for indicator in out["bias_indicators"]:
        label = pretty(indicator["technique"])
        if label not in techniques:
            techniques.append(label)
    bias_body = (
        '<p class="detected">Detected:</p>' + "".join(f'<div class="tech">{esc(name)}</div>' for name in techniques[:5])
        if techniques
        else '<p class="empty">No persuasion indicators. The wording appears neutral.</p>'
    )
    if not out["media"]:
        media_rows = [("Audio analysis", "Not submitted"), ("Video analysis", "Not submitted")]
        media_note = "No media was submitted, so no authenticity statement is made."
    else:
        media_rows = [(item["media_ref"], pretty(item["status"])) for item in out["media"]]
        media_note = out["media"][0]["note"]
    media_body = "".join(f'<div class="media-line"><b>{esc(name)}</b><span>{esc(state)}</span></div>' for name, state in media_rows)
    media_body += f'<p class="note">{esc(media_note)}</p>'

    level = overall_uncertainty(out["assessments"])
    steps = "".join(f"<li>{esc(step)}</li>" for step in out["next_steps"][:4]) or "<li>No further steps recorded.</li>"
    uncertainty = (
        f'<p class="tag">Overall uncertainty</p><div class="bar"><div class="fill" style="width:{UNCERTAINTY_WIDTH[level]}%"></div></div>'
        f'<span class="pill">{esc(uncertainty_word(level))}</span>'
        f'<p class="kicker red" style="margin-top:16px">RECOMMENDED NEXT STEPS</p><ul class="steps">{steps}</ul>'
    )
    evidence_map = (
        '<div class="map">'
        f'<div><div class="kicker green">SUPPORTING</div>{source_list(sources_for(out["evidence"], "supports"))}</div>'
        '<div class="hub"><div class="node"></div><div class="stem"></div>'
        f'<div class="conclusion"><div class="kicker teal">CONCLUSION</div>{esc(short_conclusion(out["assessments"]))}</div></div>'
        f'<div><div class="kicker red">CONTRADICTORY</div>{source_list(sources_for(out["evidence"], "contradicts"))}</div>'
        "</div>"
    )
    st.markdown(
        f'<div class="grid3">{card("CLAIM EXTRACTION", claim_body)}{card("BIAS & PERSUASION", bias_body)}{card("MEDIA AUTHENTICITY", media_body)}</div>'
        f'<div class="grid2">{card("EVIDENCE MAP", evidence_map)}{card("UNCERTAINTY & NEXT STEPS", uncertainty)}</div>',
        unsafe_allow_html=True,
    )


def render_claims(out: dict) -> None:
    evidence = {item["id"]: item for item in out["evidence"]}
    assessments = {item["claim_id"]: item for item in out["assessments"]}
    blocks = []
    for claim in out["claims"]:
        assessment = assessments.get(claim["id"])
        verdict = pretty(assessment["verdict"]) if assessment else "Not assessed"
        uncertainty = uncertainty_word(assessment["uncertainty"]) if assessment else "n/a"
        cited = (assessment["supporting_evidence"] + assessment["contradicting_evidence"] + assessment["context_evidence"]) if assessment else []
        cites = []
        for evidence_id in cited:
            record = evidence[evidence_id]
            relation = record["relationships"].get(claim["id"], "")
            cites.append(
                f'<div class="src"><b>[{esc(evidence_id)}] {esc(pretty(relation))}</b> — {esc(record["title"])}<br>“{esc(record["excerpt"])}”</div>'
            )
        reasoning = esc(assessment["reasoning"]) if assessment else ""
        blocks.append(
            f'<div class="split">{card(claim["id"].upper(), f"<p class=claim>{esc(claim['text'])}</p><span class=pill>{esc(verdict)}</span> <span class=tag>{esc(uncertainty)} uncertainty</span><p class=note>{reasoning}</p>")}'
            f'{card("CITED EVIDENCE", "".join(cites) or "<p class=empty>No evidence cited.</p>")}</div>'
        )
    st.markdown(f'<div class="stack">{"".join(blocks) or "<p class=empty>No claims.</p>"}</div>', unsafe_allow_html=True)


def render_evidence(out: dict) -> None:
    blocks = []
    for record in out["evidence"]:
        relations = ", ".join(f"{claim_id}: {pretty(relation)}" for claim_id, relation in record["relationships"].items())
        blocks.append(
            card(
                record["id"].upper(),
                f'<p class="claim">{esc(record["title"])}</p><p>“{esc(record["excerpt"])}”</p>'
                f'<p class="note">{esc(record.get("publisher") or "Unknown publisher")} · {esc(record.get("published_at") or "undated")} · {esc(relations)}</p>',
            )
        )
    st.markdown(f'<div class="stack">{"".join(blocks) or "<p class=empty>No evidence was retrieved.</p>"}</div>', unsafe_allow_html=True)


def render_media(out: dict) -> None:
    if not out["media"]:
        body = '<p class="empty">No audio or video was submitted. Nothing was analyzed, and no authenticity claim is made.</p>'
    else:
        body = "".join(
            f'<div class="media-line"><b>{esc(item["media_ref"])}</b><span>{esc(pretty(item["status"]))}</span></div><p class="note">{esc(item["note"])}</p>'
            for item in out["media"]
        )
    st.markdown(card("MEDIA AUTHENTICITY", body), unsafe_allow_html=True)


def render_bias(out: dict) -> None:
    if not out["bias_indicators"]:
        st.markdown(card("BIAS & PERSUASION", '<p class="empty">No indicators detected. The wording appears neutral.</p>'), unsafe_allow_html=True)
        return
    blocks = [
        card(
            pretty(item["technique"]).upper(),
            f'<p class="quote">“{esc(item["span"]["text"])}”</p><p>{esc(item["explanation"])}</p><p class="note">{esc(item["caution"])}</p>',
        )
        for item in out["bias_indicators"]
    ]
    st.markdown(f'<div class="stack">{"".join(blocks)}</div>', unsafe_allow_html=True)


def render_networks(out: dict) -> None:
    graph = out.get("provenance")
    if not graph:
        st.markdown(card("NETWORK", '<p class="empty">Provenance was not requested for this run.</p>'), unsafe_allow_html=True)
        return
    st.markdown(card("PROVENANCE NETWORK", '<p class="note">Each arrow is a recorded relationship. Open a row to read why it was drawn.</p>'), unsafe_allow_html=True)
    labels = {node["id"]: f"{pretty(node['type'])}: {node['label'][:70]}" for node in graph["nodes"]}
    visible = {"input_media", "claim", "evidence_source", "excerpt", "conclusion", "hypothesis", "analysis_finding"}
    keep = {node["id"] for node in graph["nodes"] if node["type"] in visible}

    def dot(text: str) -> str:
        return '"' + " ".join(str(text).split()).replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = [
        'digraph G { rankdir=LR; bgcolor="#07090d"; '
        'node [shape=box, style="rounded,filled", fillcolor="#141a22", color="#e23b3b", fontcolor="#e6e8ee", fontsize=10]; '
        'edge [color="#8b93a1", fontcolor="#c5cad3", fontsize=8];'
    ]
    for node_id in sorted(keep):
        lines.append(f"{dot(node_id)} [label={dot(labels[node_id])}];")
    rows = []
    for edge in graph["edges"]:
        if edge["source"] in keep and edge["target"] in keep:
            lines.append(f"{dot(edge['source'])} -> {dot(edge['target'])} [label={dot(edge['type'])}];")
        rows.append(
            f"<tr><td>{esc(labels.get(edge['source'], edge['source']))}</td><td class=tech>{esc(edge['type'])}</td>"
            f"<td>{esc(labels.get(edge['target'], edge['target']))}</td><td>{esc(edge['basis'])}</td></tr>"
        )
    lines.append("}")
    st.graphviz_chart("\n".join(lines), width="stretch")
    st.markdown(
        '<div class="card" style="overflow:auto"><h3>EDGES</h3><table style="width:100%;border-collapse:collapse;font-size:0.85rem">'
        + "".join(rows[:40])
        + "</table></div>",
        unsafe_allow_html=True,
    )


def render_reports(result: dict) -> None:
    out = result["output"]
    questions = "".join(f"<li>{esc(item)}</li>" for item in out["unresolved_questions"]) or "<li>None recorded.</li>"
    limits = "".join(f"<li>{esc(item)}</li>" for item in out["limitations"])
    agents = "".join(
        f'<div class="media-line"><b>{esc(agent["name"])}</b><span>{esc(pretty(agent["status"]))}</span></div><p class="note">{esc(agent["detail"])}</p>'
        for agent in result["agents"]
    )
    summary = esc(out["summary"])
    st.markdown(
        f'<div class="stack">{card("SUMMARY", f"<p>{summary}</p>")}'
        f'{card("UNRESOLVED QUESTIONS", f"<ul class=steps>{questions}</ul>")}'
        f'{card("LIMITATIONS", f"<ul class=steps>{limits}</ul>")}'
        f'{card("AGENTS", agents)}</div>',
        unsafe_allow_html=True,
    )
    st.download_button("Download JSON", data=json.dumps(result, indent=2), file_name=f"{result['run_id']}.json", mime="application/json")


info = health()
page_labels = [label for _, label in PAGES]
page_ids = [page_id for page_id, _ in PAGES]

with st.sidebar:
    st.markdown('<p class="brand">VERITAS</p>', unsafe_allow_html=True)
    current = page_ids[page_labels.index(st.radio("Sections", page_labels, label_visibility="collapsed"))]
    st.divider()
    if info is None:
        st.markdown(f'<p class="tech">API unreachable at {esc(API_URL)}</p>', unsafe_allow_html=True)
    else:
        st.caption(f"API v{info['version']}")
    if st.button("Clear"):
        st.session_state.pop("result", None)
        st.session_state.pop("case_title", None)
        st.rerun()


def render_configure(api_info: dict | None) -> None:
    sample_default = bool((api_info or {}).get("sample_mode_default"))
    with st.container(key="config_panel"):
        st.markdown('<div class="casehead"><span class="inv">CONFIGURE</span><span class="case">Investigation</span></div>', unsafe_allow_html=True)
        with st.form("investigate", border=False, clear_on_submit=False):
            query = st.text_input("Question", placeholder="What should be checked?")
            content = st.text_area("Passage or transcript", height=88, placeholder="Optional. Leave empty to check the question alone.")
            page_col, video_col = st.columns(2)
            url = page_col.text_input(
                "Page link",
                placeholder="https://… article or page",
                help="Public http or https page. Fetched only when Sample mode is off. Private and internal addresses are refused.",
            )
            media_url = video_col.text_input(
                "Video link",
                placeholder="https://… audio or video",
                help="Recorded with the case. No detector is configured, so authenticity is not judged.",
            )
            mode_col, review_col, memory_col, run_col = st.columns([1.15, 1.15, 1.15, 1], vertical_alignment="bottom")
            sample_mode = mode_col.toggle("Sample mode", value=sample_default, help="On uses the bundled fictional case and calls no live service. Off follows .env.")
            include_review = review_col.toggle("NVIDIA review", value=True)
            use_memory = memory_col.toggle("H-MEM memory", value=True)
            run = run_col.form_submit_button("Run", type="primary", disabled=api_info is None, width="stretch")
    if not run:
        return
    if not query.strip():
        st.markdown(card("QUESTION NEEDED", "<p class='empty'>Enter a question, then run the investigation.</p>"), unsafe_allow_html=True)
        return
    submit_investigation(query, content, url, media_url, sample_mode, include_review, use_memory)
    st.rerun()


def render_dashboard_shell() -> None:
    st.markdown(
        f'<div class="grid3">{card("CLAIM EXTRACTION", "<p class=empty>Run an investigation to extract claims.</p>")}'
        f'{card("BIAS & PERSUASION", "<p class=empty>Persuasion indicators appear here.</p>")}'
        f'{card("MEDIA AUTHENTICITY", "<p class=empty>Attach a video link to record it. Nothing is judged authentic or fake.</p>")}</div>'
        f'<div class="grid2">{card("EVIDENCE MAP", "<p class=empty>Supporting and contradictory sources appear here.</p>")}'
        f'{card("UNCERTAINTY & NEXT STEPS", "<p class=empty>Uncertainty and recommended next steps appear here.</p>")}</div>',
        unsafe_allow_html=True,
    )


def submit_investigation(query: str, content: str, url: str, media_url: str, sample_mode: bool, include_review: bool, use_memory: bool) -> dict:
    payload = {
        "input": {
            "query": query,
            **({"content": content} if content.strip() else {}),
            **({"url": url} if url.strip() else {}),
            **({"media_url": media_url} if media_url.strip() else {}),
        },
        "options": {
            "sample_mode": sample_mode,
            "include_nvidia_review": include_review,
            "use_memory": use_memory,
            "include_provenance": True,
            "include_media_analysis": bool(media_url.strip()),
        },
    }
    with st.spinner("Investigating…"):
        try:
            response = httpx.post(f"{API_URL}/api/v1/investigate", json=payload, timeout=480)
            st.session_state["result"] = response.json()
            st.session_state["case_title"] = query.strip()
        except (httpx.HTTPError, ValueError):
            st.session_state["result"] = {"status": "error", "error": {"code": "request_failed", "message": "The API could not be reached."}, "trace_id": ""}
    return st.session_state["result"]


def render_board(result: dict | None) -> None:
    if result and result.get("status") == "error":
        error = result.get("error", {})
        st.markdown(card("ERROR", f'<p class="tech">{esc(error.get("code", "error"))}</p><p>{esc(error.get("message", "The investigation failed."))}</p>'), unsafe_allow_html=True)
        return
    if not result:
        st.markdown('<div class="casehead"><span class="inv">DASHBOARD</span><span class="case">Waiting for a run</span></div>', unsafe_allow_html=True)
        render_dashboard_shell()
        return
    title = esc(st.session_state.get("case_title") or "Investigation")
    demo = '<p class="demo">Demonstration. Bundled fictional data. No live service was called.</p>' if result["output"].get("sample_mode") else ""
    st.markdown(f'<div class="casehead"><span class="inv">DASHBOARD</span><span class="case">{title}</span></div>{demo}', unsafe_allow_html=True)
    render_dashboard(result)


result = st.session_state.get("result")
if current == "dashboard":
    render_configure(info)
    render_board(result)
elif result and result.get("status") == "error":
    st.markdown(card("ERROR", f'<p class="tech">{esc(result["error"]["code"])}</p><p>{esc(result["error"]["message"])}</p>'), unsafe_allow_html=True)
elif not result:
    st.markdown(card("READY", "<p class='empty'>Run an investigation from the Dashboard.</p>"), unsafe_allow_html=True)
elif current == "claims":
    render_claims(result["output"])
elif current == "evidence":
    render_evidence(result["output"])
elif current == "media":
    render_media(result["output"])
elif current == "bias":
    render_bias(result["output"])
elif current == "networks":
    render_networks(result["output"])
else:
    render_reports(result)

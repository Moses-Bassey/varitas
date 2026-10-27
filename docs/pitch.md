# VERITAS — pitch outline

## 1. The problem (30 s)
Misleading content rarely lies outright. It pairs a real statistic with an unsupported cause, adds urgency, and
quotes an unnamed expert. Fact-checks that only say "false" lose the audience, because readers cannot see why.

## 2. The idea (30 s)
VERITAS breaks content into claims, flags persuasion techniques in the exact words used, retrieves evidence, and
links every conclusion back to the source excerpts behind it in a provenance graph. Each edge records its basis.

## 3. Live demo (2 min) — sample mode, fully offline
1. `docker run --rm -p 8000:8000 -e SAMPLE_MODE=true veritas`, or run the dashboard.
2. Investigate the fictional Varnholm desalination post.
3. Show what comes back:
   - "fish catches fell 40%" is **supported**: a fisheries report gives 38%.
   - "the plant is destroying the harbor" has **insufficient evidence**: a quota cut came first, and the salinity
     rise is local to the outfall.
   - "a leading scientist confirmed the brine is toxic" is **contradicted**.
4. Open the provenance graph and follow a verdict back to its excerpt and source.
5. Point out the banner: every source in the demo is fictional and labelled as such.

## 4. How the sponsors fit (1 min)
- **Zetaris**: the evidence data layer. Each run issues read-only SQL over the direct REST API; query ids go into
  the trace.
- **Meterless Nightshift**: the reasoning model. It extracts claims, analyses bias, compares evidence, and writes
  the summary. Output is accepted only when its quotes anchor in the source text.
- **Meterless H-MEM**: investigative memory. VERITAS runs the official reference engine unmodified, and it passes
  13/13 conformance checks. Recalled memories are shown as context, never as evidence.
- **NVIDIA NIM**: an independent second reviewer. Disagreements are recorded and raise uncertainty; they never
  silently change the verdict.

## 5. Trust and safety (30 s)
- No guilt, intent, identity, or affiliation is inferred.
- Bias indicators describe wording only.
- Media is reported as not analyzed, because no detector is configured.
- URL fetching is SSRF-safe and respects robots.txt.
- Logs are secret-masked.

## 6. What is verified vs. not (30 s)
State exactly what README section 10 says. Do not claim live sponsor results that were not run.

## 7. Next steps
- A media detector behind the existing `media_detector_result` node.
- Larger curated Zetaris corpora.
- Human-review workflow on unresolved questions.

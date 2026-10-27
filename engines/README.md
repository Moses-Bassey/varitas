# Meterless engines used by VERITAS

`hmem/` is the official Meterless H-MEM reference implementation and conformance suite, copied unmodified from
[Meterless/Meterless](https://github.com/Meterless/Meterless) at commit `1742dec56a66e58eef9dda2d2b865823758ca8cd`
(path `engines/hmem`). It is licensed under Apache-2.0; see `hmem/LICENSE`. Only `reference/`, `conformance/`,
`AGENTS.md` and `LICENSE` were copied.

`hmem-sidecar/server.ts` is VERITAS code. It runs the reference `HMEM` class in a Node process and exposes its
`add`, `query`, `feedback` and ledger calls over HTTP so the Python API can use them. It contains no memory logic.

The reference engine documents its own limits: the embedder is a deterministic hash mock, and storage is a JSON
file. Treat retrieval scores as the reference engine's output, not as semantic similarity from a production embedder.

Run the official conformance suite against the vendored engine:

```powershell
cd engines/hmem-sidecar
npm install
npm run conformance
```

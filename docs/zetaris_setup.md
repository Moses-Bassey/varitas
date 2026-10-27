# Zetaris setup for VERITAS

VERITAS reads evidence from one Zetaris table over the direct REST channel documented in the official Zetaris
getting-started kit (`scripts/zetaris_sql.py`, `RestChannel`):

- `POST {ZETARIS_REST_URL}/sql-editor/sqls/run`
- Headers: `Authorization: Bearer $ZETARIS_API_KEY`, `X-Org-ID: $ZETARIS_ORG_ID`, `X-Request-ID: <uuid4>`, `Content-Type: application/json`
- Body: `{"queryId": "<uuid4>", "sql": "<one statement>", "source": "SqlEditor", "limit": <n>}`
- Rows come back in the response's `data` field as lists, in the column order of the `SELECT`.

VERITAS only issues read-only `SELECT` statements. User text never reaches SQL except as lowercase alphanumeric
keywords, which are checked against `^[a-z0-9]{3,30}$` before use.

## Evidence table schema

Register a table (for example `veritas.evidence_sources`) with these columns. All are read as strings.

| column | meaning |
| --- | --- |
| `source_id` | Stable unique id of the document |
| `title` | Document title |
| `url` | Public URL, or empty |
| `publisher` | Organisation that published it |
| `published_at` | ISO date, or empty |
| `source_kind` | `primary`, `secondary`, or empty |
| `content` | Plain-text body (VERITAS uses the first 20,000 characters) |

VERITAS runs queries of this shape:

```sql
SELECT source_id, title, url, publisher, published_at, source_kind, content
FROM veritas.evidence_sources
WHERE lower(content) LIKE '%harbor%' OR lower(content) LIKE '%salinity%'
```

Creating the table and loading documents is a write. Do it yourself in the Zetaris console or with the official
helper's write mode, for any source you are licensed to use. VERITAS never writes to Zetaris.

## Configure and check

```powershell
# .env
ZETARIS_REST_URL=https://<your-zetaris-host>/api/v1.0
ZETARIS_API_KEY=<key>
ZETARIS_ORG_ID=<org id>
ZETARIS_EVIDENCE_TABLE=veritas.evidence_sources
```

With the API running in live mode, `GET /api/v1/integrations/status` runs `SELECT 1` through the same channel and
reports `online`, `unauthorized` (bad key, HTTP 401), `http_400` (bad org id or request id), or `unreachable`.

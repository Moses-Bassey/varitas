// HTTP shim over the official Meterless H-MEM reference engine
// (engines/hmem/reference, Apache-2.0, vendored unmodified). Every route calls
// the HMEM facade directly; this file adds transport, validation and auth only.

import http from "node:http";
import { HMEM } from "../hmem/reference/src/index.ts";
import type { MemoryLayer, MemoryRecord, MemoryType, ProvenanceOrigin } from "../hmem/reference/src/index.ts";

const HOST = process.env.HMEM_HOST ?? "127.0.0.1";
const PORT = Number(process.env.HMEM_PORT ?? 8787);
const PERSIST_DIR = process.env.HMEM_PERSIST_DIR ?? "./data";
const TOKEN = process.env.HMEM_SIDECAR_TOKEN ?? "";
const MAX_BODY = 256 * 1024;
const ENGINE = "meterless-hmem-reference";

const TYPES: MemoryType[] = ["personal", "factual", "preference", "general"];
const LAYERS: MemoryLayer[] = ["short_term", "working", "long_term"];
const ORIGINS: ProvenanceOrigin[] = [
  "chat_message", "user_correction", "file_save", "file_open", "file_edit",
  "model_response", "plan_completion", "document", "manual", "dream", "sleep_synthesis",
];
const FEEDBACK = ["helpful", "not_helpful", "wrong"] as const;

const hmem = new HMEM({ persistDir: PERSIST_DIR, actor: "veritas" });

class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

function publicRecord(record: MemoryRecord): Omit<MemoryRecord, "embedding"> {
  const { embedding: _embedding, ...rest } = record;
  return rest;
}

function str(value: unknown, field: string, max = 4000): string {
  if (typeof value !== "string" || value.trim() === "") throw new HttpError(400, `${field} must be a non-empty string`);
  if (value.length > max) throw new HttpError(400, `${field} exceeds ${max} characters`);
  return value;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], field: string, fallback: T): T {
  if (value === undefined) return fallback;
  if (typeof value === "string" && (allowed as readonly string[]).includes(value)) return value as T;
  throw new HttpError(400, `${field} must be one of ${allowed.join(", ")}`);
}

function readBody(req: http.IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_BODY) {
        reject(new HttpError(413, "request body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      try {
        const parsed = JSON.parse(Buffer.concat(chunks).toString("utf-8") || "{}");
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not an object");
        resolve(parsed as Record<string, unknown>);
      } catch {
        reject(new HttpError(400, "body must be a JSON object"));
      }
    });
    req.on("error", reject);
  });
}

function send(res: http.ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
}

async function route(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  const url = new URL(req.url ?? "/", "http://sidecar.local");

  if (req.method === "GET" && url.pathname === "/health") {
    send(res, 200, { status: "ok", engine: ENGINE, records: hmem.store.all().length, ledger: hmem.ledger.stats() });
    return;
  }

  if (TOKEN && req.headers.authorization !== `Bearer ${TOKEN}`) throw new HttpError(401, "unauthorized");

  if (req.method === "POST" && url.pathname === "/v1/memories") {
    const body = await readBody(req);
    const provenance = (body.provenance ?? {}) as Record<string, unknown>;
    const confidence = typeof body.confidence === "number" ? body.confidence : 0.5;
    if (confidence < 0 || confidence > 1) throw new HttpError(400, "confidence must be within [0, 1]");
    const tags = Array.isArray(body.tags) ? body.tags.filter((t): t is string => typeof t === "string").slice(0, 20) : [];
    const refs = Array.isArray(provenance.refs) ? provenance.refs.filter((r): r is string => typeof r === "string").slice(0, 20) : undefined;
    const record = hmem.add({
      content: str(body.content, "content"),
      type: oneOf(body.type, TYPES, "type", "factual"),
      layer: oneOf(body.layer, LAYERS, "layer", "working"),
      tags,
      confidence,
      source: str(body.source, "source", 500),
      chatId: typeof body.chatId === "string" ? body.chatId : undefined,
      provenance: {
        origin: oneOf(provenance.origin, ORIGINS, "provenance.origin", "document"),
        learnedAt: Date.now(),
        label: typeof provenance.label === "string" ? provenance.label.slice(0, 300) : "recorded by VERITAS",
        refs,
      },
    });
    send(res, 201, { engine: ENGINE, memory: publicRecord(record) });
    return;
  }

  if (req.method === "POST" && url.pathname === "/v1/query") {
    const body = await readBody(req);
    const topN = typeof body.topN === "number" ? Math.min(20, Math.max(1, Math.floor(body.topN))) : undefined;
    const threshold = typeof body.threshold === "number" ? Math.min(1, Math.max(0, body.threshold)) : undefined;
    const result = hmem.query(str(body.text, "text"), { topN, threshold, chatId: typeof body.chatId === "string" ? body.chatId : undefined });
    send(res, 200, {
      engine: ENGINE,
      memories: result.memories.map((m) => ({ memory: publicRecord(m.memory), relevance: m.relevance })),
      context: result.context,
      trace: result.trace,
    });
    return;
  }

  if (req.method === "POST" && url.pathname === "/v1/feedback") {
    const body = await readBody(req);
    const memoryId = str(body.memoryId, "memoryId", 100);
    if (!hmem.store.get(memoryId)) throw new HttpError(404, "unknown memory id");
    hmem.feedback(memoryId, oneOf(body.kind, FEEDBACK, "kind", "helpful"));
    send(res, 200, { engine: ENGINE, memory: publicRecord(hmem.store.get(memoryId)!) });
    return;
  }

  if (req.method === "GET" && url.pathname === "/v1/ledger") {
    const memoryId = url.searchParams.get("memoryId");
    const entries = memoryId ? hmem.ledger.history(memoryId) : hmem.ledger.all().slice(-200);
    send(res, 200, { engine: ENGINE, entries });
    return;
  }

  throw new HttpError(404, "not found");
}

const server = http.createServer((req, res) => {
  route(req, res).catch((err: unknown) => {
    if (err instanceof HttpError) send(res, err.status, { error: err.message });
    else if (err instanceof Error && err.message.startsWith("H-MEM:")) send(res, 400, { error: err.message });
    else send(res, 500, { error: "internal error" });
  });
});

server.listen(PORT, HOST, () => {
  console.log(JSON.stringify({ event: "hmem_sidecar_started", engine: ENGINE, host: HOST, port: PORT, auth: Boolean(TOKEN) }));
});

# Node.js Backend

## Runtime choice

- **Node LTS** (currently 24 active LTS, 22 in maintenance; 26 is Current and becomes LTS Oct 2026 — it ships Temporal enabled by default and undici 8): default for production backends. Largest ecosystem compatibility, slowest-moving, best observability story. Pin the major in `package.json` `engines` and `.nvmrc`/`.node-version`; CI must run the pinned version. From Node 27 the release cycle is annual and every major reaches LTS after six months as Current.
- **Bun**: fast installs/startup/test runner; fine for tooling, scripts, and apps you've load-tested on it. Verify native-addon and edge-case Node-API compat before betting production on it.
- **Deno**: strong security model (permission flags), built-in TS. Choose when its model fits; ecosystem friction has shrunk with npm compat but still exists.
- Decision rule: pick per-project, write runtime-neutral code (Web APIs: `fetch`, Web Streams, Web Crypto, `AbortController`) so the choice stays reversible. Avoid runtime-specific APIs in shared libraries.

## Use the platform — drop unnecessary deps

Node now ships what used to require packages. Every dep removed is supply-chain and maintenance surface removed.

| Dependency | Built-in replacement (Node ≥20/22) |
|---|---|
| axios/node-fetch/got | global `fetch` (undici) |
| nodemon | `node --watch` |
| dotenv | `node --env-file=.env` (≥20.6) |
| jest/mocha (for libs/simple apps) | `node:test` + `node:assert` |
| chalk (basic) | `styleText` from `node:util` |
| uuid | `crypto.randomUUID()` |
| minimist/yargs (simple CLIs) | `util.parseArgs` |
| glob (simple) | `fs.glob` (≥22) |
| ws client (basic) | global `WebSocket` (≥22) |

```bash
node --watch --env-file=.env src/server.ts   # Node ≥22.18/≥24 runs TS directly (type stripping, unflagged)
node --test --experimental-test-coverage
```

Keep deps that earn their weight (pino, zod, drizzle/prisma, fastify). The bar: a dep must do something nontrivial that the platform doesn't.

## Env and config: parse once, crash fast

Never sprinkle `process.env.X` through the codebase — env vars are `string | undefined`, typos are silent, and defaults scatter.

```ts
// config.ts — the only file allowed to touch process.env
import { z } from 'zod';

const Env = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']),
  PORT: z.coerce.number().int().min(1).max(65535).default(3000),
  DATABASE_URL: z.string().url(),
  LOG_LEVEL: z.enum(['debug', 'info', 'warn', 'error']).default('info'),
  STRIPE_KEY: z.string().min(1),
});

const parsed = Env.safeParse(process.env);
if (!parsed.success) {
  console.error('Invalid environment:', parsed.error.flatten().fieldErrors);
  process.exit(1);   // crash at boot, not at 3am when the code path is hit
}
export const config = Object.freeze(parsed.data);
```

- Boot-time crash on bad config is a feature: the orchestrator restarts and alerts; a lazy crash mid-request loses data.
- Secrets never in code or committed `.env`; inject via secret manager/orchestrator. `.env` is local-dev only and gitignored.
- No `NODE_ENV === 'production'` branches scattered in logic — derive named flags in config (`config.isDev`) and branch on those.

## HTTP server hardening

Defaults are unsafe: Node's http server has lenient timeouts; frameworks accept huge bodies.

```ts
import { createServer } from 'node:http';
const server = createServer(app);
server.requestTimeout = 30_000;       // whole request (default 300s — too long)
server.headersTimeout = 10_000;       // slowloris defense (must be < requestTimeout)
server.keepAliveTimeout = 65_000;     // > LB idle timeout (ALB 60s) to avoid 502 races
server.maxRequestsPerSocket = 1000;
```

- **Body limits**: cap JSON/body size at the framework (`express.json({ limit: '100kb' })`, Fastify `bodyLimit`). Unbounded bodies = trivial memory DoS. Cap per route by actual need.
- **Behind a proxy**: set `trust proxy` correctly (exact hop count, not `true`) or rate limiting keys on spoofable `X-Forwarded-For`.
- **Headers**: `helmet` (or hand-set: `HSTS`, `X-Content-Type-Options: nosniff`, frame-ancestors via CSP). Disable `X-Powered-By`.
- **Rate limit** auth and expensive endpoints (`rate-limiter-flexible` backed by Redis when multi-instance — in-memory limits don't survive horizontal scaling).
- **Validation**: every route parses body/query/params with a schema before touching them (rules/01, rules/05). Fastify + zod type-provider, or tRPC, makes this structural.
- **Compression**: gate on response size; never compress encrypted/random content; beware BREACH when reflecting secrets in compressed responses.
- Prefer Fastify over Express for new services: schema-first validation, structured logging built in (pino), 2-3× throughput, maintained.

## Graceful shutdown

Kubernetes/ECS send SIGTERM and wait (default 30s) before SIGKILL. Dropping in-flight requests on deploy is a self-inflicted incident.

```ts
const server = app.listen(config.PORT);
const shutdown = async (signal: string) => {
  logger.info({ signal }, 'shutting down');
  server.close(() => logger.info('http closed'));        // stop accepting, finish in-flight
  server.closeIdleConnections();
  setTimeout(() => { logger.error('forced exit'); process.exit(1); }, 25_000).unref();
  await jobQueue.stop();          // stop pulling new work
  await db.end();                 // then close pools
  process.exit(0);
};
process.on('SIGTERM', () => void shutdown('SIGTERM'));
process.on('SIGINT', () => void shutdown('SIGINT'));
```

Order matters: (1) fail readiness probe / stop accepting, (2) drain in-flight with a deadline shorter than the orchestrator's, (3) close DB/queue/redis, (4) exit 0. `setTimeout(...).unref()` so the safety timer doesn't itself hold the process open. Long-lived connections (SSE/WebSocket) need explicit termination — `server.close` waits for them forever; track and end them.

## Health checks and readiness

Liveness ≠ readiness. Liveness: "is the process alive" — answer cheaply, no dependency checks (a DB blip must not get you killed and restarted in a loop). Readiness: "should I receive traffic" — checks pool health, returns 503 during shutdown drain.

```ts
let ready = true;                       // flipped false first thing in shutdown()
app.get('/healthz', (_req, res) => res.status(200).send('ok'));
app.get('/readyz', async (_req, res) => {
  if (!ready) return res.status(503).send('draining');
  const dbOk = await db.ping().then(() => true, () => false);
  res.status(dbOk ? 200 : 503).send(dbOk ? 'ok' : 'db');
});
```

Flip readiness to 503 at the start of shutdown, then wait one probe period before `server.close()` so the LB stops routing first — this is what makes zero-downtime deploys actually zero-downtime.

## Request context: AsyncLocalStorage

Propagate request ID / user / trace context without threading a `ctx` parameter through every signature.

```ts
import { AsyncLocalStorage } from 'node:async_hooks';
const requestContext = new AsyncLocalStorage<{ reqId: string; userId?: string }>();

app.use((req, _res, next) => {
  requestContext.run({ reqId: req.headers['x-request-id'] ?? crypto.randomUUID() }, next);
});

// anywhere downstream — no parameter drilling
export const log = (obj: object, msg: string) =>
  logger.info({ ...requestContext.getStore(), ...obj }, msg);
```

It survives `await`, timers, and promise chains. Use it for logging context and tracing only — not as a grab-bag service locator (hidden dependencies become untestable). OpenTelemetry's Node SDK rides the same mechanism; adopt OTel for traces rather than hand-rolling.

## Process-level error policy

```ts
process.on('unhandledRejection', (reason) => {
  logger.fatal({ err: reason }, 'unhandled rejection');
  throw reason;   // escalate to uncaughtException path — same policy
});
process.on('uncaughtException', (err) => {
  logger.fatal({ err }, 'uncaught exception — exiting');
  // flush logs/telemetry synchronously if needed, then:
  process.exit(1);
});
```

Policy: **log, then die**. After an uncaught exception the process state is undefined (half-finished writes, corrupted singletons) — continuing risks data corruption worse than a restart. The orchestrator's job is restarting; your job is exiting loudly. Never install an `uncaughtException` handler that swallows and continues (HIGH finding). Per-request errors belong in framework error handlers — they must never reach the process level.

- `process.on('warning')` → log it (catches MaxListenersExceeded, deprecations).
- Don't call `process.exit()` in normal flow — it skips pending I/O and `finally` blocks; let the loop drain or use exit codes from the shutdown path only.

## Structured logging: pino

`console.log` in services is unsearchable, unleveled, and synchronous-ish under load. pino writes newline-JSON, fast, with levels and redaction.

```ts
import { pino } from 'pino';
export const logger = pino({
  level: config.LOG_LEVEL,
  redact: { paths: ['req.headers.authorization', 'req.headers.cookie', '*.password', '*.token'], censor: '[redacted]' },
  // dev only: transport: { target: 'pino-pretty' }
});

// Structured fields first, message second — never string-interpolate data
logger.info({ userId, orderId, durationMs }, 'order created');
logger.error({ err }, 'payment failed');   // `err` key serializes stack + cause chain
```

- One child logger per request with a request ID: `req.log = logger.child({ reqId })` (Fastify does this automatically). Propagate the ID to downstream calls (`AsyncLocalStorage` for context without parameter drilling).
- Redact secrets at the logger, not by hoping call sites remember.
- Never log: tokens, passwords, full card numbers, raw request bodies on auth routes, PII beyond need.
- `pino-pretty` is a dev dependency/CLI, not production config. Production emits raw JSON to stdout; the platform ships it.

## Worker pools and not blocking the loop

One Node process = one JS thread. A 200ms synchronous task means every concurrent request waits 200ms.

- Known CPU work (hashing, image resize, PDF gen, big JSON.parse, compression): `piscina` worker pool (rules/03). Size pool ≈ cores − 1.
- `crypto.scrypt`/`bcrypt` async variants use the libuv threadpool — never the `*Sync` variants in request paths. Bump `UV_THREADPOOL_SIZE` (default 4!) if crypto/DNS/fs-heavy.
- Banned in request paths: `fs.*Sync`, `child_process.execSync`, `zlib.*Sync`, `JSON.parse` of multi-MB payloads (cap body size instead), synchronous template rendering of huge documents.
- Detect blocking in production: monitor event-loop delay with `perf_hooks.monitorEventLoopDelay()` (alert at p99 > ~100ms), or `blocked-at` in staging to get stacks.
- Multi-core: prefer N processes via the orchestrator (K8s replicas) over in-process `cluster`; keep processes single-purpose.

## Background work in services

- In-process `setInterval` jobs are lost on restart, duplicated across replicas, and drift. Anything that must run exactly/at-least once per schedule belongs in a job queue (BullMQ on Redis, pg-boss on Postgres, or the platform's scheduler) with: idempotent handlers, explicit retry/backoff policy, dead-letter handling, and per-job timeouts.
- If a lightweight in-process ticker is genuinely fine (cache refresh, metrics flush): wrap the callback in try/catch (a thrown error in a bare `setInterval` callback is an uncaught exception → process exit policy kicks in), `.unref()` it so it can't hold shutdown, and guard against overlap (skip if previous run still in flight).

```ts
let running = false;
const timer = setInterval(() => {
  if (running) return;
  running = true;
  refreshCache().catch(e => logger.error({ err: e }, 'refresh failed')).finally(() => { running = false; });
}, 30_000);
timer.unref();
```

- Long-running request work (report generation, imports): return `202 Accepted` + job ID + status endpoint; don't hold an HTTP request open for minutes against every timeout in the chain.

## Outbound calls: pools, timeouts, retries

Your service is only as reliable as its slowest dependency. Every outbound call gets:
- **Timeout**: `AbortSignal.timeout(ms)` on fetch; statement timeout on DB queries. No infinite waits — a hung upstream plus no timeout equals your own outage.
- **Bounded retries with jittered backoff**, idempotent operations only; honor `Retry-After`. Retrying non-idempotent POSTs duplicates orders — use idempotency keys.
- **Connection pooling**: undici `Agent`/`Pool` for high-volume HTTP to fixed origins; DB pool sized deliberately (start ~10 per instance; pool_size × instances must stay under the DB's max_connections — the default-100 Postgres ceiling is hit by autoscaling, not load).
- **Circuit breaking** on flapping dependencies (opossum) so you fail fast instead of queueing doomed work.

```ts
const res = await fetch(upstream, { signal: AbortSignal.timeout(3000) });
if (res.status >= 500) throw new UpstreamError(res.status);   // retry layer decides
```

## Native ESM in Node

- `"type": "module"` in package.json. `__dirname`/`__filename` don't exist — use `import.meta.dirname` / `import.meta.filename` (Node ≥20.11), or `new URL('./file', import.meta.url)` for asset paths.
- JSON imports: `import data from './data.json' with { type: 'json' }`.
- Don't mix: a stray `require` in ESM throws; CJS deps import fine via default import. Publishing libraries: ship ESM; add CJS only if your consumers truly need it (use tsup/unbuild dual output, verify with `attw`).
- Dynamic `import()` works in both module systems — it's the migration bridge and the lazy-loading tool.

## Audit checklist

- [ ] `grep -rn "process.env" src/ --include="*.ts" | grep -v "config\|env.ts"` — env access outside the config module (MEDIUM); no schema validation of env at boot (HIGH).
- [ ] `grep -rn "Sync(" src/ | grep -v "test\|script"` — `*Sync` calls in server code (HIGH in request paths).
- [ ] Server timeouts: `grep -rn "headersTimeout\|requestTimeout\|keepAliveTimeout" src/` — absent = slowloris-exposed defaults (MEDIUM).
- [ ] Body limits configured (`grep -rn "bodyLimit\|limit:" src/`) — unbounded body parsing (HIGH, DoS).
- [ ] `grep -rn "SIGTERM" src/` — no graceful shutdown handler = dropped requests on every deploy (MEDIUM).
- [ ] `grep -rn "uncaughtException" src/` — handler that doesn't exit (HIGH); no `unhandledRejection` policy at all (MEDIUM).
- [ ] `grep -rn "console.log\|console.error" src/ | grep -v test` — in services, replace with pino (LOW; MEDIUM if logging objects with secrets).
- [ ] Logger redaction configured? `grep -rn "redact" src/` — logging auth headers/bodies without redaction (HIGH).
- [ ] `grep -rn "trust proxy" src/` and rate-limiter keying — spoofable client IP (MEDIUM).
- [ ] `package.json`: `engines.node` pinned; deps that duplicate platform built-ins (axios, dotenv, uuid, nodemon) — removable (LOW).
- [ ] `grep -rn "bcrypt.hashSync\|scryptSync\|pbkdf2Sync" src/` — sync crypto in request path (HIGH).
- [ ] `grep -rn "process.exit" src/ | grep -v "config\|shutdown"` — exits mid-flow skipping cleanup (MEDIUM).
- [ ] Readiness vs liveness probes distinct; readiness flips during drain (`grep -rn "readyz\|readiness" src/`) — single do-everything healthcheck (LOW/MEDIUM).
- [ ] Outbound fetch/DB calls without timeouts (`grep -rn "fetch(" src/ | grep -v signal`; DB client statement_timeout) — MEDIUM, HIGH for critical paths.
- [ ] Retry logic on non-idempotent operations without idempotency keys (MEDIUM/HIGH if money).
- [ ] DB pool size × replica count vs database max_connections — documented anywhere? (LOW).

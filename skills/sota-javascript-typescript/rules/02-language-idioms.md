# Language Idioms & Pitfalls

## Equality and coercion

- Always `===`/`!==`. `==` coercion rules are unmemorizable (`[] == false`, `'' == 0`, `null == undefined` all true). Single allowed exception: `x == null` to test null-or-undefined at once — but `x === null || x === undefined` or `x ?? fallback` is clearer; just ban `==` entirely (`eslint eqeqeq: ["error", "always"]`).
- `Object.is` only for `NaN`/`-0` distinction. `NaN === NaN` is false; use `Number.isNaN(x)` — never the global `isNaN`, which coerces (`isNaN('foo')` is true).
- `Number.isInteger`, `Number.isFinite` over global counterparts for the same reason.

## Nullish discipline: `??` and `?.`

`||` treats `0`, `''`, `false` as missing. `??` only treats `null`/`undefined` as missing.

```ts
// BAD — port 0 and empty prefix silently replaced
const port = config.port || 3000;
const prefix = config.prefix || '/api';

// GOOD
const port = config.port ?? 3000;
const retries = opts.retries ?? 3;        // retries: 0 respected
el.count ??= 0;                            // nullish assignment
```

Optional chaining rules:
- `?.` is for genuinely-optional data, not for silencing the compiler. A long chain `a?.b?.c?.d` usually means the type is wrong or validation was skipped upstream — fix the source.
- `x?.()` for optional callbacks; `arr?.[i]` for optional indexing.
- Don't combine `?.` with non-null assertion `!` — pick one truth. `!` is banned in app code except immediately after an explicit check the compiler can't see (document why); prefer restructuring so narrowing works.
- Remember `a?.b.c` short-circuits the whole chain when `a` is nullish — `.c` is safe; but `(a?.b).c` is not.

## Array method selection

Pick the method that states intent; reviewers read methods faster than loop bodies.

| Need | Use | Not |
|---|---|---|
| transform each | `map` | `forEach` + push |
| keep some | `filter` | manual loop |
| first match | `find` / `findIndex` / `findLast` | `filter(...)[0]` |
| any/all match | `some` / `every` | `filter(...).length > 0` |
| reduce to one value | `reduce` (sparingly) | — |
| flatten + map | `flatMap` | `map(...).flat()` |
| membership | `includes` | `indexOf !== -1` |
| group | `Object.groupBy` / `Map.groupBy` (ES2024) | reduce boilerplate |
| index from end | `at(-1)` | `arr[arr.length - 1]` |

- `forEach` only for pure side effects; it ignores return values and cannot `await` correctly (`forEach(async ...)` fires-and-forgets every iteration — classic bug; use `for...of` with `await`, or `Promise.all(arr.map(...))` for parallel).
- `reduce` building objects/arrays with spread per iteration is O(n²) — use a mutable accumulator inside the reduce or a plain loop.
- Early-exit needs: `some`/`every`/`find` short-circuit; `map`/`filter` don't — use `for...of` when you must break out of a transform.
- Don't chain `filter().map()` over hot million-element arrays; one `for...of` or `flatMap` pass is fine. Below that scale, readability wins.

## Immutability patterns

Mutating shared data causes spooky action at a distance and breaks React/state-library change detection.

```ts
// BAD — sort/reverse/splice mutate in place
const sorted = users.sort((a, b) => a.age - b.age);   // also reordered `users`!

// GOOD — ES2023 change-by-copy methods
const sorted = users.toSorted((a, b) => a.age - b.age);
const reversed = items.toReversed();
const without = items.toSpliced(i, 1);
const updated = items.with(i, newItem);
```

- Mutators to flag on shared/parameter arrays: `sort`, `reverse`, `splice`, `push/pop/shift/unshift`, `fill`, `copyWithin`. Local arrays you just created may be mutated freely — purity at the boundary, pragmatism inside.
- Deep copy: `structuredClone(obj)` — handles Dates, Maps, Sets, cycles, typed arrays. Never `JSON.parse(JSON.stringify(x))` (drops `undefined`, functions, Dates become strings, throws on cycles). Note structuredClone drops functions and prototypes — data only.
- Shallow update idiom: `{ ...obj, field: v }` / `[...arr, item]` — shallow is fine when nested values are themselves replaced, not mutated.
- Declare `readonly` arrays/properties in signatures; `as const` for fixed tables. `Object.freeze` is shallow and dev-only value — types are the real enforcement.
- `let` is a smell outside loops/accumulators; `const` everywhere (`prefer-const` lint).

## Map/Set over object-as-map

Objects as dictionaries inherit `Object.prototype` (`'toString' in obj` is true!), stringify all keys, and are the prototype-pollution sink.

```ts
// BAD
const cache: Record<string, User> = {};
if (cache[name]) ...        // breaks for name = "constructor"

// GOOD
const cache = new Map<string, User>();
cache.set(name, user);
cache.get(name);
```

- `Map`: arbitrary key types, `.size`, guaranteed insertion order, faster frequent add/delete, no prototype hazards.
- `Set` for membership: `seen.has(x)` is O(1) vs `arr.includes(x)` O(n). Dedupe: `[...new Set(arr)]`.
- If an object truly must be a dictionary (JSON shape), create it via `Object.create(null)` or always guard with `Object.hasOwn(obj, key)` (ES2022 — replaces `obj.hasOwnProperty`).
- `WeakMap`/`WeakSet` to associate data with objects without preventing GC (e.g., DOM node metadata, memoization keyed by object).
- `Map.prototype.getOrInsert(key, default)` / `getOrInsertComputed(key, fn)` (V8 14.6: Node 26, Chrome 146+) replace the check-then-set dance for cache/grouping maps — use where your runtime floor allows.
- `Record<string, T>` indexing under `noUncheckedIndexedAccess` correctly yields `T | undefined` — Map's `.get` was always honest about this.

## Error handling

Never throw strings or plain objects — they lose stack traces and break `instanceof` routing.

```ts
// BAD
throw 'user not found';
throw { code: 404 };
catch (e) { console.log(e); throw new Error('failed: ' + e); }   // stack lost

// GOOD — subclass + cause chain
class NotFoundError extends Error {
  constructor(public readonly resource: string, public readonly id: string, opts?: ErrorOptions) {
    super(`${resource} ${id} not found`, opts);
    this.name = 'NotFoundError';
  }
}

try {
  await db.query(sql);
} catch (e) {
  throw new NotFoundError('user', id, { cause: e });   // ES2022 cause preserves the chain
}
```

Rules:
- `catch (e)` is `unknown` — narrow with `instanceof` before reading `.message`. Helper for the rest: `const toError = (e: unknown): Error => e instanceof Error ? e : new Error(String(e), { cause: e });`
- Set `this.name` in subclasses; route on `instanceof` or a `code` field, never on message text.
- Always pass `{ cause: e }` when wrapping — loggers (pino) serialize the chain.
- Never swallow: empty `catch {}` is a finding unless commented with why. Catch only where you can handle or add context; otherwise let it propagate.
- `finally` for cleanup; or ES2026 explicit resource management: `using conn = await pool.acquire()` with `[Symbol.asyncDispose]` (use `await using` for async disposal) — adopt where the runtime/tsconfig supports it.

Result-style for expected failures: exceptions for bugs/infra, values for domain outcomes the caller must handle.

```ts
type Result<T, E> = { ok: true; value: T } | { ok: false; error: E };

async function parsePrice(input: string): Promise<Result<Cents, 'invalid' | 'negative'>> { /* ... */ }

const r = await parsePrice(raw);
if (!r.ok) return showError(r.error);   // compiler forces the check
use(r.value);
```

Use Result (hand-rolled discriminated union, or neverthrow if the team wants combinators) for validation, parsing, business-rule failures. Don't Result-ify everything — infra errors (DB down) should still throw. ES2025 `try`-expression proposals aside, today the union is the idiom. `safeParse` from zod is exactly this pattern.

## Iterators and generators

- Generators for lazy/infinite/paginated sequences — they avoid materializing intermediate arrays:

```ts
async function* paginate(url: string): AsyncGenerator<Item> {
  let next: string | null = url;
  while (next) {
    const page = await fetchPage(next);
    yield* page.items;
    next = page.nextUrl;
  }
}
for await (const item of paginate(api)) { if (matches(item)) break; }  // stops fetching early
```

- ES2025 iterator helpers: `Iterator.from(it).filter(f).map(g).take(10).toArray()` — lazy chaining without arrays.
- Make domain collections iterable via `[Symbol.iterator]` rather than exposing internal arrays.
- Caveat: generators are single-pass; spreading one consumes it. Don't iterate twice.

## Proxy caution

`Proxy` is for frameworks (Vue reactivity, immer), not application code. Costs: every property access pays a trap-call penalty; identity breaks (`proxy !== target`); `this`-binding bugs with private fields and built-ins (Map/Date methods throw through naive proxies); devtools/debugging opacity. If you reach for Proxy in app code, the answer is almost always an explicit function, a class, or a Map. `Reflect.*` belongs inside proxy handlers, rarely elsewhere.

## Dates: Temporal, and surviving without it

`Date` is mutable, months are 0-indexed, parsing is implementation-defined, and it has no timezone besides local/UTC. Temporal (Stage 4 March 2026, part of ES2026; shipped in Chrome/Edge 144+, Firefox 139+, and enabled by default in Node 26) fixes all of it — immutable, explicit types:

```ts
// GOOD — Temporal (Safari still hasn't shipped it — use `temporal-polyfill` for web targets)
const meeting = Temporal.ZonedDateTime.from('2026-03-08T09:00[America/New_York]');
const later = meeting.add({ hours: 2 });                       // DST-safe
const dur = end.since(start);                                  // Temporal.Duration
const today = Temporal.Now.plainDateISO('Europe/Prague');
```

Type selection: `Instant` for timestamps, `PlainDate` for calendar dates (birthdays — no timezone!), `PlainTime`, `ZonedDateTime` for wall-clock + zone, `Duration` for spans. Choosing the right type eliminates the bug class.

Until Temporal is available everywhere you ship (Node ≥26 backends: it is; browser code: polyfill until Safari ships): store/transmit UTC ISO-8601 strings or epoch ms; convert at display; use date-fns (tree-shakeable) over dayjs/moment (moment is dead — flag it). Never do arithmetic by adding `86400000` — DST days are 23/25h.

## Number precision and money

- All JS numbers are float64: `0.1 + 0.2 !== 0.3`; integers exact only to `Number.MAX_SAFE_INTEGER` (2^53−1). 64-bit DB IDs and Twitter snowflakes silently corrupt as numbers — keep them strings.
- Money: integer minor units (cents) in a branded type, never floats.

```ts
type Cents = Brand<number, 'Cents'>;
const total = (items: readonly Cents[]) => items.reduce((a, b) => (a + b) as Cents, 0 as Cents);
const display = (c: Cents) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(c / 100);
```

- Division/percentages: decide the rounding rule explicitly (`Math.round` half-away-from-zero vs banker's), document it, test it. Allocation (splitting $10 three ways) must distribute the remainder, not round each share.
- `BigInt` for >2^53 integers (crypto, snowflakes, wei). Don't mix with `number` (`1n + 1` throws); `JSON.stringify` throws on BigInt — serialize as string.
- High-precision decimals (rates, FX): a decimal library (`decimal.js`/`big.js`) or do the math in the database. The TC39 Decimal proposal isn't shipped.
- `parseFloat`/`parseInt` accept garbage prefixes (`parseInt('12px')` → 12). Prefer `Number(str)` + `Number.isFinite` check, or zod `z.coerce.number()`. Always pass radix if you do use `parseInt(s, 10)`.

## Functions and modules over classes; classes where they earn it

Default unit of design: pure functions + plain data (typed objects), composed in modules. Classes earn their place for: stateful long-lived things with invariants (connection pools, caches), Error subclasses, when a framework expects them. Avoid:

- Classes as namespaces (all-static members) — use a module.
- Single-implementation interfaces + DI-container ceremony in app code — pass dependencies as function/constructor parameters directly; introduce the interface when the second implementation (or the test fake) actually exists.
- Inheritance for code reuse — compose; `extends` only for genuine is-a with stable base (Error, framework bases). Deep hierarchies in JS are refactor glue traps.
- Getters with side effects or surprise allocation; getters that throw.

```ts
// BAD — class-as-namespace + hidden temporal coupling
class UserService { static db: Db; static async get(id: string) { return this.db.find(id); } }

// GOOD — explicit deps, trivially testable
export const makeUserService = (db: Db) => ({
  get: (id: UserId) => db.find(id),
  // ...
});
export type UserService = ReturnType<typeof makeUserService>;
```

Module hygiene: no side effects at import time (registrations, connections, reading env) outside the composition root — importing a module should be safe and free. Side-effectful imports break tree-shaking, tests, and tooling.

## Strings and Unicode

- `str.length` counts UTF-16 code units, not characters: `'👨‍👩‍👧'.length === 8`. Iterate by code point (`[...str]`, `for...of`) for character-ish ops; grapheme-correct counting/truncation needs `Intl.Segmenter`:

```ts
const seg = new Intl.Segmenter('en', { granularity: 'grapheme' });
const truncate = (s: string, n: number) => [...seg.segment(s)].slice(0, n).map(x => x.segment).join('');
```

- Normalize before comparing user-entered text: `a.normalize('NFC') === b.normalize('NFC')` ('é' has two encodings).
- Locale-aware comparison/sorting: `Intl.Collator`/`localeCompare`, never `<` on strings for human-facing sort. All formatting (numbers, dates, lists, plurals) via `Intl.*` — never hand-rolled `${day}/${month}` strings.
- `replaceAll` over `replace(/g/)` for literal replacement (no regex-escaping bugs). When building a RegExp from user input is unavoidable, escape it (`RegExp.escape` (ES2025) or the well-known escape helper) — see rules/05 ReDoS.
- Multi-line template literals respect indentation — use `dedent` or keep them flush-left; don't ship accidental leading whitespace in SQL/emails.

## Audit checklist

- [ ] `grep -rn "[^=!]==[^=]\|!=[^=]" --include="*.ts" src/` — loose equality (MEDIUM; eqeqeq lint).
- [ ] `grep -rn "|| 0\||| ''\||| \[\]\||| {}" src/` and `\b(port|count|index|limit|offset|retries)\s*=.*||` — `||` where `??` is meant (MEDIUM, HIGH if money/ports).
- [ ] `grep -rn "forEach(async" src/` — fire-and-forget async iteration (HIGH).
- [ ] `grep -rn "\.sort(\|\.reverse(\|\.splice(" src/` — verify each operates on a locally-owned array, else `toSorted`/`toReversed`/`toSpliced` (MEDIUM).
- [ ] `grep -rn "JSON.parse(JSON.stringify" src/` — replace with `structuredClone` (LOW/MEDIUM).
- [ ] `grep -rn "throw ['\"\`]\|throw {" src/` — thrown non-Errors (HIGH).
- [ ] `grep -rn "catch ([a-z]*)\s*{\s*}" src/` and `catch.*{\s*$` followed by `}` — swallowed errors (HIGH).
- [ ] `grep -rn "new Error(.*+\|new Error(\`" src/` — wrapping without `{ cause }` (LOW).
- [ ] `grep -rn "hasOwnProperty" src/` — use `Object.hasOwn` (LOW).
- [ ] `grep -rn "price\|amount\|total\|balance" --include="*.ts" src/ | grep -i "float\|\* 0\.\|/ 100\|toFixed"` — float money math (HIGH).
- [ ] `grep -rn "from 'moment'\|require('moment')" src/` — dead library; migrate (MEDIUM). `new Date(` arithmetic with `86400000`/`3600000` constants — DST bugs (MEDIUM).
- [ ] `grep -rn "!" --include="*.tsx" -l src/` then targeted `grep -rn "\w!\.\|\w!;" src/` — non-null assertions; each needs justification (MEDIUM in app code).
- [ ] ESLint: `eqeqeq`, `prefer-const`, `no-param-reassign`, `@typescript-eslint/no-floating-promises`, `no-non-null-assertion` configured.

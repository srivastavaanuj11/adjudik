# 0007. M4 web dashboard choices

Date: 2026-11-11
Status: accepted

## Context

SPEC §12 and §16.2 fix the dashboard's screens, the review workspace, the §12.4
standards and the production image, but leave some mechanics to the implementer,
and one choice revisits a deferral recorded in ADR 0006. Per SPEC §1 rule 4 we
record the choices and the deviations.

## Decision

1. **The typed client is hand-authored over the committed OpenAPI, not
   generated from it.** SPEC §12.4 asks for a typed client generated from
   OpenAPI, and ADR 0006 deferred the endpoint annotation to M4. The server's
   endpoints are hand-rolled DRF `APIView`s without serializers, so
   `drf-spectacular` emits a valid document of paths and operations
   (`schemas/openapi.json`, committed) but no response components — a generated
   client would be untyped. Rather than retrofit serializers onto ~50 endpoints
   (invasive, and a risk to the 360 green server tests), the dashboard's types
   live in `web/src/api/types.ts`, mirroring the documented response shapes, and
   are consumed through a small `fetch` wrapper (`client.ts`) with CSRF and the
   §11.1 error envelope. The Playwright flows (§17) exercise these shapes against
   the real API, so a drift surfaces there. Serializer-backed component typing
   remains a later cleanup; the generated `openapi.json` stays the path
   inventory and is served at `GET /api/schema/`.

2. **Production serves the SPA through a catch-all view plus WhiteNoise.** The
   build sets Vite `base` to `/static/` (build only; the dev server stays at
   `/`), so built, content-hashed assets resolve under `/static/` and are served
   by WhiteNoise. A regex catch-all (`^(?!api/|healthz|readyz|static/|media/)`)
   returns the built `index.html` for any other path, so SPA deep links work on a
   full-page load (SPEC §16.2). In development the catch-all is inert (no build
   present) and Vite serves the SPA.

3. **`collectstatic` uses WhiteNoise's compressed, non-manifest storage in
   production.** Vite already fingerprints asset filenames, so Django's manifest
   hashing is redundant and would break the view-served `index.html` (whose asset
   references are not rewritten through the manifest). `settings.prod` therefore
   uses `CompressedStaticFilesStorage` rather than the base
   `CompressedManifestStaticFilesStorage`.

4. **The Test tab uses a plain JSON textarea, not CodeMirror.** SPEC §12.1 names
   a CodeMirror editor. To avoid a heavy editor dependency for M4, the Test tab
   is a monospace `<textarea>` with live JSON-parse validation and the "Rules
   only" / "Full run" buttons. The behaviour (edit `sample_input`, dry-run,
   render the result) is unchanged; a richer editor can replace it later.

5. **The evaluation page shows "input not available" rather than a purge date.**
   SPEC §12.1 wants "Input purged on <date>". The evaluation detail response does
   not carry `input_purged_at` (only the purge endpoint returns it), so the page
   shows the input when present and otherwise states it is unavailable (purged or
   not stored). Surfacing the exact date needs a server field, left for a later
   pass.

6. **Inline style attributes and the production CSP.** The spec's production CSP
   (§15.7) is `default-src 'self'; object-src 'none'; frame-ancestors 'none'`,
   with no `style-src`, so inline `style` attributes are restricted under it. The
   dashboard's structural styling is in a same-origin stylesheet (allowed); the
   remaining inline styles are cosmetic spacing. Moving them to classes (or
   adding a scoped `style-src`) is a later cleanup and does not affect the e2e
   flows, which run under `settings.dev`.

7. **`seed_demo` gains a fixed-password option and a second admin for e2e.** A
   `--password` flag (or `$ADJUDIK_SEED_PASSWORD`) gives every demo user a known
   password so Playwright can sign in, and a second admin (`demo-admin2`) lets
   the four-eyes approval flow be exercised. Defaults are unchanged (random
   per-user passwords, no fixed value), so the §16.5 seed test is unaffected.

## Consequences

- The dashboard is fully typed without touching the server, and the typed layer
  is the single place to update if a response shape changes; the Playwright
  flows guard against drift.
- The same image serves the API and the SPA, with working deep links, and builds
  clean; development is unchanged (Vite at `/`).
- Three small spec deviations (CodeMirror, the purge date, inline-style CSP) are
  recorded as later cleanups; none affects the M4 §17 acceptance flows.

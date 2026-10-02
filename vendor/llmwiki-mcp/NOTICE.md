# Vendored: llmwiki MCP server (bundled)

`bin.bundle.cjs` is a single-file bundle of the `@llmwiki/core` MCP stdio
server (`packages/core` → `dist/mcp/bin.js` and its runtime dependencies:
`@modelcontextprotocol/sdk`, `gray-matter`), built with `esbuild`
(`--bundle --platform=node --target=node20 --format=cjs`) from:

- Source: https://github.com/microsoft/llmwiki
- Commit: b44df6ae95138d0edcbcc79b5b1d099c78bce5e0
- Package version: @llmwiki/core@0.1.2
- License: MIT (Copyright (c) Microsoft Corporation) — see upstream repo
  for full license text.

## Why this is vendored instead of cloned/built per session

`@llmwiki/core` is not published to npm (confirmed: the README's
`npx -y -p @llmwiki/core` instructions don't resolve), so earlier setup
cloned the source repo and ran `npm install` + `tsc` in the SessionStart
hook (`.claude/hooks/session-start.sh`) on every session start, racing
against the MCP client's stdio-connection timeout. That race caused
intermittent `CONNECTION_CLOSED` failures — the clone+install+build
sometimes didn't finish before the client gave up waiting for the
handshake, regardless of the order of steps inside the hook (reordering
was tried twice before and only narrowed the race, it didn't close it).

Bundling the built server into one dependency-free file and committing it
here removes the network fetch, `npm install`, and `tsc` build from the
session-start critical path entirely: `.mcp.json` now runs
`node vendor/llmwiki-mcp/bin.bundle.cjs .wiki` directly, which starts in
milliseconds.

## Updating

To refresh this bundle after an upstream change:

```bash
git clone --depth 1 https://github.com/microsoft/llmwiki.git /tmp/llmwiki-src
cd /tmp/llmwiki-src && npm install --workspace=packages/core && npm run build --workspace=packages/core
npx esbuild /tmp/llmwiki-src/packages/core/dist/mcp/bin.js \
  --bundle --platform=node --target=node20 --format=cjs \
  --outfile=vendor/llmwiki-mcp/bin.bundle.cjs
```

Then update the commit/version noted above.

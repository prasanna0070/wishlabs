// Writes .br and .gz copies next to every compressible file in dist/ (run after `vite build`). gstudio-web serves
// them by Accept-Encoding (web/server/app/main.py static_file), so the SPA ships compressed without a compression
// middleware: the chat SSE stream and WebSockets are never buffered. A copy is kept only when it saves at least 10%.
//   node scripts/precompress.mjs [distDir]
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { brotliCompressSync, constants, gzipSync } from "node:zlib";

const dir = process.argv[2] || new URL("../dist", import.meta.url).pathname;
const TYPES = /\.(js|mjs|css|html|svg|json|txt|xml|wasm|glb|ttf|otf|ico)$/i;
const MIN = 1024;

function* walk(d) {
  for (const name of readdirSync(d)) {
    const p = join(d, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else yield p;
  }
}

let files = 0;
let before = 0;
let after = 0;
for (const p of walk(dir)) {
  if (!TYPES.test(p)) continue;
  const raw = readFileSync(p);
  if (raw.length < MIN) continue;
  const br = brotliCompressSync(raw, { params: { [constants.BROTLI_PARAM_QUALITY]: 11, [constants.BROTLI_PARAM_SIZE_HINT]: raw.length } });
  const gz = gzipSync(raw, { level: 9 });
  if (br.length <= raw.length * 0.9) writeFileSync(`${p}.br`, br);
  if (gz.length <= raw.length * 0.9) writeFileSync(`${p}.gz`, gz);
  files += 1;
  before += raw.length;
  after += Math.min(br.length, raw.length);
}
console.log(`precompress: ${files} files, ${(before / 1024).toFixed(0)} KB -> ${(after / 1024).toFixed(0)} KB with brotli`);

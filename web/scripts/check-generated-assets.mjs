import { existsSync, readFileSync, readdirSync } from "node:fs";
import { relative, resolve, sep } from "node:path";
import { brotliCompressSync, constants } from "node:zlib";

const root = resolve(import.meta.dirname, "../../oddsfox_graph/static/explorer");
for (const required of ["index.html", "assets"]) {
  if (!existsSync(resolve(root, required))) {
    throw new Error(`Missing generated explorer asset: ${required}`);
  }
}

const files = readdirSync(root, { recursive: true, withFileTypes: true })
  .filter((entry) => entry.isFile())
  .map((entry) => {
    const path = resolve(entry.parentPath, entry.name);
    return { path, relativePath: relative(root, path).split(sep).join("/") };
  });
for (const file of files) {
  const normalized = file.relativePath.toLocaleLowerCase();
  if (normalized.endsWith(".wasm") || normalized.includes("duckdb") || normalized.endsWith(".parquet")) {
    throw new Error(`Removed static runtime artifact is still bundled: ${file.relativePath}`);
  }
}

const html = readFileSync(resolve(root, "index.html"), "utf8");
const entryMatch = html.match(/<script[^>]+src="\.\/(assets\/index-[^"]+\.js)"/);
if (!entryMatch) throw new Error("Unable to identify the generated explorer entry chunk");
const compressedEntryBytes = brotliCompressSync(
  readFileSync(resolve(root, entryMatch[1])),
  { params: { [constants.BROTLI_PARAM_QUALITY]: 11 } },
).byteLength;
if (compressedEntryBytes > 70 * 1024) {
  throw new Error(`Explorer entry chunk exceeds 70 KiB Brotli: ${compressedEntryBytes} bytes`);
}
for (const deferredSurface of ["Analyst-", "Presentation-"]) {
  if (!files.some((file) => file.relativePath.startsWith(`assets/${deferredSurface}`))) {
    throw new Error(`${deferredSurface.slice(0, -1)} is not emitted as a deferred chunk`);
  }
}

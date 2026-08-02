import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import process from "node:process";

const paths = process.argv.slice(2);
if (paths.length === 0) throw new Error("At least one generated path is required");

const repositoryRoot = resolve(import.meta.dirname, "../..");
const status = execFileSync(
  "git",
  ["status", "--porcelain=v1", "--untracked-files=all", "--", ...paths],
  { cwd: repositoryRoot, encoding: "utf8" },
).trim();

if (status) {
  throw new Error(`Generated files are missing or stale:\n${status}`);
}

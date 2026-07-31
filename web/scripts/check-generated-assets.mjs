import { existsSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../../oddsfox_graph/static/explorer");
for (const required of ["index.html", "assets"]) {
  if (!existsSync(resolve(root, required))) {
    throw new Error(`Missing generated explorer asset: ${required}`);
  }
}

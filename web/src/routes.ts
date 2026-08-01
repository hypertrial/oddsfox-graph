import type { ExploreRoute } from "./Explore";

export type Route = ExploreRoute | { kind: "analyst" };

export function parseRoute(hash: string): Route {
  const value = hash.replace(/^#/, "").replace(/^\//, "");
  const [path, query = ""] = value.split("?", 2);
  if (!path || path === "explore") return { kind: "home" };
  if (path === "compare") {
    const initialClaimId = new URLSearchParams(query).get("claim") ?? undefined;
    return initialClaimId ? { kind: "compare", initialClaimId } : { kind: "compare" };
  }
  if (path === "analyst") return { kind: "analyst" };
  const parts = path.split("/").map(decodeURIComponent);
  if (parts[0] !== "explore" || parts.length !== 3 || !parts[2]) return { kind: "home" };
  if (parts[1] === "stage") return { kind: "stage", id: parts[2] };
  if (parts[1] === "team") return { kind: "team", id: parts[2] };
  if (parts[1] === "market") return { kind: "market", id: parts[2] };
  if (parts[1] === "relationship") return { kind: "relationship", id: parts[2] };
  return { kind: "home" };
}

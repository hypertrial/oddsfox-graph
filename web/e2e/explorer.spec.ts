import { expect, test } from "@playwright/test";

const node = {
  id: "yes-1",
  label: "Alpha happens",
  level: "proposition",
  parent_id: "event-alpha",
  x: 0,
  y: 0,
  size: 8,
  domain: "sports",
  component_id: "component-one",
  market_id: "market-one",
  proposition_count: 1,
  edge_count: 0,
  classification_coverage: 1,
};

const nodeTwo = {
  ...node,
  id: "yes-2",
  label: "Beta happens",
  parent_id: "event-beta",
  market_id: "market-two",
  x: 20,
};

const recordingEdge = {
  id: "proposal-one",
  source: "yes-1",
  target: "yes-2",
  relation: "implies",
  count: 1,
  confidence: 0.99,
  discovery_method: "deterministic",
  evidence_tier: "deterministic_rule",
  aggregation_only: false,
};

const scoreBreakdown = {
  confidence: 0.99,
  scope: 1,
  structural_reach: 1,
  evidence_interest: 0.8,
  relation_interest: 1,
  confidence_contribution: 0.297,
  scope_contribution: 0.25,
  structural_reach_contribution: 0.2,
  evidence_interest_contribution: 0.12,
  relation_interest_contribution: 0.1,
  base_importance: 0.967,
  same_relation_count: 0,
  same_evidence_tier_count: 0,
  same_event_pair_count: 0,
  shared_endpoint_count: 0,
  same_relation_penalty: 0,
  same_evidence_tier_penalty: 0,
  same_event_pair_penalty: 0,
  shared_endpoint_penalty: 0,
  total_penalty: 0,
  selection_score: 0.967,
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    let body: unknown;
    if (url.pathname.endsWith("/meta")) {
      body = {
        package_version: "0.11.0",
        viewer: { build_mode: "fast", validation_status: "DETERMINISTIC_VALIDATED", evidence_tiers: ["source_contract", "deterministic_rule"] },
        coverage: { classification_coverage: 0.98, all_market_selection: true },
        build: { build_mode: "fast", validation_status: "DETERMINISTIC_VALIDATED" },
      };
    } else if (url.pathname.endsWith("/overview")) {
      body = { level: "event", nodes: [], edges: [], truncated_nodes: false, truncated_edges: false, coverage: {} };
    } else if (url.pathname.endsWith("/recording-plan")) {
      body = {
        schema_version: "oddsfox-recording-plan-v1",
        ranking_version: "balanced-logic-edge-v1",
        graph_fingerprint: "fixture",
        mode: "fast",
        validation_status: "DETERMINISTIC_VALIDATED",
        requested_limit: 6,
        min_confidence: 0.95,
        eligible_edge_count: 1,
        candidate_pool_size: 1,
        highlights: [{
          rank: 1,
          proposal_id: "proposal-one",
          source_id: "yes-1",
          source_label: "Alpha happens",
          source_market_id: "market-one",
          source_event_key: "event-alpha",
          source_domain: "sports",
          target_id: "yes-2",
          target_label: "Beta happens",
          target_market_id: "market-two",
          target_event_key: "event-beta",
          target_domain: "sports",
          relation: "implies",
          confidence: 0.99,
          evidence_tier: "deterministic_rule",
          discovery_method: "deterministic",
          explanation_excerpt: "Alpha entails Beta in the accepted logical model.",
          importance_score: 0.967,
          score_breakdown: scoreBreakdown,
        }],
        graph: { level: "proposition", nodes: [node, nodeTwo], edges: [recordingEdge], truncated_nodes: false, truncated_edges: false, coverage: {} },
        context_pruning: { incident_edge_cap_per_endpoint: 25, candidate_nodes: 2, candidate_edges: 1, retained_nodes: 2, retained_edges: 1, pruned_nodes: 0, pruned_edges: 0 },
      };
    } else if (url.pathname.endsWith("/search")) {
      body = [{ node_id: "yes-1", market_id: "market-one", outcome_label: "Yes", event_slug: "event-alpha", canonical_proposition: "Alpha happens" }];
    } else if (url.pathname.endsWith("/subgraph")) {
      body = { level: "proposition", nodes: [node], edges: [], truncated_nodes: false, truncated_edges: false, coverage: {} };
    } else if (url.pathname.includes("/nodes/")) {
      body = { node, edges: [] };
    } else if (url.pathname.endsWith("/prove")) {
      body = [{ from_node_id: "yes-1", to_node_id: "yes-2", steps: [], hops: 1, bottleneck_confidence: 0.99 }];
    } else if (url.pathname.endsWith("/why-not")) {
      body = { status: "not_retrieved", explanation: "Pair was not retrieved" };
    } else {
      body = {};
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
});

test("explores a result and runs bounded reasoning tools", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Logic Explorer" })).toBeVisible();
  await expect(page.getByText("98.0%" )).toBeVisible();

  await page.getByLabel("Search markets and propositions").fill("Alpha");
  await page.getByRole("button", { name: "Search" }).click();
  await page.getByRole("button", { name: /Alpha happens/ }).click();
  await expect(page.getByText("yes-1", { exact: true })).toBeVisible();

  await page.getByLabel("To proposition").fill("yes-2");
  await page.getByRole("button", { name: "Prove path" }).click();
  await expect(page.getByText(/bottleneck_confidence/)).toBeVisible();

  await page.getByRole("button", { name: "Explain absence" }).click();
  await expect(page.getByText(/not_retrieved/)).toBeVisible();
});

test("previews and seeks the automatic deterministic story", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Auto story" }).click();
  await expect(page.getByText("Deterministic logical highlights")).toBeVisible();
  await page.waitForFunction(() => window.__ODDSFOX_RECORDING__?.ready === true);
  expect(await page.evaluate(() => window.__ODDSFOX_RECORDING__?.getFrameCount())).toBe(390);
  await page.evaluate(() => window.__ODDSFOX_RECORDING__?.seek(180));
  await expect(page.getByRole("heading", { name: "Alpha happens implies Beta happens" })).toBeVisible();
  await expect(page.getByText("99.0% confidence")).toBeVisible();
  await page.getByRole("button", { name: "Exit presentation" }).click();
  await expect(page.getByRole("heading", { name: "Logic Explorer" })).toBeVisible();
});

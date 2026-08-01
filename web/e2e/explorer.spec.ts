import { expect, test } from "@playwright/test";

const winnerYes = {
  id: "brazil-winner-yes",
  market_id: "brazil-winner",
  canonical_team_name: "Brazil",
  stage_key: "winner",
  stage_rank: 5,
  normalized_progression_level: 5,
  question: "Will Brazil win the 2026 FIFA World Cup?",
  answer: "Yes",
  plain_claim: "Brazil wins the World Cup",
  is_progression_token: true,
  market_status: "active",
  is_still_alive: true,
  market_close_epoch: 1784419200,
  technical_canonical_label: "Brazil wins the World Cup",
};

const winnerNo = {
  ...winnerYes,
  id: "brazil-winner-no",
  answer: "No",
  plain_claim: "Brazil does not win the World Cup",
  is_progression_token: false,
  technical_canonical_label: "NOT(Brazil wins the World Cup)",
};

const finalYes = {
  ...winnerYes,
  id: "brazil-final-yes",
  market_id: "brazil-final",
  stage_key: "final",
  stage_rank: 4,
  normalized_progression_level: 4,
  market_close_epoch: 1784332800,
  question: "Will Brazil reach the 2026 FIFA World Cup final?",
  plain_claim: "Brazil reaches the final",
  technical_canonical_label: "Brazil reaches the final",
};

const finalNo = {
  ...finalYes,
  id: "brazil-final-no",
  answer: "No",
  plain_claim: "Brazil does not reach the final",
  is_progression_token: false,
  technical_canonical_label: "NOT(Brazil reaches the final)",
};

const winnerMarket = {
  market_id: "brazil-winner",
  event_slug: "brazil-world-cup-winner",
  question: winnerYes.question,
  canonical_team_name: "Brazil",
  stage_key: "winner",
  stage_rank: 5,
  normalized_progression_level: 5,
  market_direction: "winner",
  market_status: "active",
  is_still_alive: true,
  market_close_epoch: 1784419200,
  claims: [winnerYes, winnerNo],
};

const finalMarket = {
  ...winnerMarket,
  market_id: "brazil-final",
  event_slug: "brazil-world-cup-final",
  question: finalYes.question,
  stage_key: "final",
  stage_rank: 4,
  normalized_progression_level: 4,
  market_direction: "advance",
  market_close_epoch: 1784332800,
  claims: [finalYes, finalNo],
};

const relationship = {
  proposal_id: "brazil-winner-implies-final",
  source: winnerYes,
  target: finalYes,
  relation: "implies",
  basis: "wc2026.progression_implication.v1",
  confidence: 1,
  evidence_tier: "deterministic_rule",
  discovery_method: "deterministic",
  explanation: "Winning the tournament is only possible after reaching and winning the final.",
};

const stages = [
  ["round_of_32", "Round of 32", 0],
  ["round_of_16", "Round of 16", 1],
  ["quarterfinal", "Quarterfinals", 2],
  ["semifinal", "Semifinals", 3],
  ["final", "Final", 4],
  ["winner", "World Cup winner", 5],
].map(([stage_key, label, rank]) => ({
  stage_key,
  label,
  stage_rank: rank,
  normalized_progression_level: rank,
  team_count: 1,
  market_count: rank === 4 || rank === 5 ? 1 : 0,
  claim_count: rank === 4 || rank === 5 ? 2 : 0,
  active_market_count: rank === 4 || rank === 5 ? 1 : 0,
  closed_market_count: 0,
  classification_eligible_count: 0,
  classification_assessed_count: 0,
  classification_status: "not_applicable",
  classification_coverage: null,
}));

const team = {
  team_key: "brazil",
  canonical_team_name: "Brazil",
  is_still_alive: true,
  market_status: "active",
  market_count: 2,
  claim_count: 4,
  stage_keys: ["final", "winner"],
  min_stage_rank: 4,
  max_stage_rank: 5,
  classification_eligible_count: 0,
  classification_assessed_count: 0,
  classification_status: "not_applicable",
  classification_coverage: null,
};

const displayStats = {
  input_node_count: 4,
  input_edge_count: 1,
  display_node_count: 4,
  display_edge_count: 1,
  omitted_edge_count: 0,
  density: 0.083,
  label_uniqueness: 1,
  max_degree: 1,
  recommended_representation: "network",
};

const graphNodes = [winnerYes, finalYes].map((claim, index) => ({
  id: claim.id,
  label: claim.plain_claim,
  level: "proposition",
  parent_id: "wc2026",
  x: index * 20,
  y: 0,
  size: 8,
  domain: "sports",
  component_id: "brazil-progression",
  market_id: claim.market_id,
  proposition_count: 1,
  edge_count: 1,
  classification_coverage: null,
  classification_status: "not_applicable",
}));

const graphEdge = {
  id: relationship.proposal_id,
  source: winnerYes.id,
  target: finalYes.id,
  relation: "implies",
  count: 1,
  confidence: 1,
  discovery_method: "deterministic",
  evidence_tier: "deterministic_rule",
  aggregation_only: false,
};

const graphView = {
  level: "proposition",
  nodes: graphNodes,
  edges: [graphEdge],
  truncated_nodes: false,
  truncated_edges: false,
  coverage: { classification_status: "not_applicable", classification_coverage: null },
  edge_mode: "essential",
  display_stats: displayStats,
};

const scoreBreakdown = {
  confidence: 1,
  stage_importance: 1,
  structural_reach: 1,
  template_novelty: 1,
  evidence_interest: 0.8,
  relation_interest: 1,
  confidence_contribution: 0.25,
  stage_importance_contribution: 0.25,
  structural_reach_contribution: 0.2,
  template_novelty_contribution: 0.15,
  evidence_interest_contribution: 0.08,
  relation_interest_contribution: 0.05,
  base_importance: 0.98,
  same_relation_count: 0,
  same_evidence_tier_count: 0,
  same_target_stage_count: 0,
  same_component_count: 0,
  same_relation_penalty: 0,
  same_evidence_tier_penalty: 0,
  same_target_stage_penalty: 0,
  same_component_penalty: 0,
  total_penalty: 0,
  selection_score: 0.98,
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    let body: unknown;
    if (url.pathname.endsWith("/meta")) {
      body = {
        package_version: "0.12.0",
        viewer: { build_mode: "fast", validation_status: "DETERMINISTIC_VALIDATED", graph_content_fingerprint: "fixture" },
        coverage: { classification_status: "not_applicable", classification_coverage: null },
        build: { build_mode: "fast", validation_status: "DETERMINISTIC_VALIDATED" },
      };
    } else if (url.pathname.endsWith("/explore")) {
      body = {
        scope: {
          source: "oddsfox-pipeline",
          scope: "wc2026",
          universe: "knockout_progression",
          selection: "all_valid_pipeline_wc2026_markets",
          input_hourly_rows: 48,
          market_count: 2,
          claim_count: 4,
          team_count: 1,
          stage_count: 6,
          first_odds_hour_epoch: 1,
          last_odds_hour_epoch: 2,
          adapter_version: "polymarket-wc2026-graph-hourly-v1",
          truncated: false,
        },
        stages,
        teams: [team],
        notable_relationships: [{ rank: 1, relationship }],
        relationship_groups: [{
          id: "wc2026-one-winner",
          title: "Only one team can win the World Cup",
          description: "All team-winner outcomes form one tournament constraint.",
          relation: "mutually_exclusive",
          member_claim_ids: [winnerYes.id],
          relationship_count: 1,
        }],
        capabilities: { mode: "live", hierarchy: true, search: true, relationship_inspection: true, analyst_graph: true, compare: true, proof: true, why_not: true, recording: true, regeneration: true },
        display_stats: displayStats,
        coverage: { classification_status: "not_applicable", classification_coverage: null },
      };
    } else if (url.pathname.endsWith("/teams/brazil")) {
      body = { summary: team, markets: [finalMarket, winnerMarket] };
    } else if (url.pathname.endsWith("/stages/final")) {
      body = { summary: stages[4], teams: [team], markets: [finalMarket] };
    } else if (url.pathname.endsWith("/markets/brazil-winner")) {
      body = winnerMarket;
    } else if (url.pathname.endsWith(`/relationships/${relationship.proposal_id}`)) {
      body = relationship;
    } else if (url.pathname.endsWith("/entity-search")) {
      const query = (url.searchParams.get("q") ?? "").toLowerCase();
      body = query.includes("final")
        ? [{ kind: "claim", id: finalYes.id, label: finalYes.plain_claim, description: "Yes outcome" }]
        : query.includes("win")
          ? [{ kind: "claim", id: winnerYes.id, label: winnerYes.plain_claim, description: "Yes outcome" }]
          : [{ kind: "team", id: "brazil", label: "Brazil", description: "2 progression markets" }];
    } else if (url.pathname.endsWith("/compare")) {
      body = { status: "direct", source: winnerYes, target: finalYes, direct: relationship, path: [], explanation: "Winning requires reaching the final." };
    } else if (url.pathname.endsWith("/overview")) {
      body = { ...graphView, layout_mode: "close_time" };
    } else if (url.pathname.endsWith("/recording-plan")) {
      body = {
        schema_version: "oddsfox-recording-plan-v2",
        ranking_version: "human-wc2026-story-edge-v2",
        graph_fingerprint: "fixture",
        mode: "fast",
        validation_status: "DETERMINISTIC_VALIDATED",
        requested_limit: 6,
        min_confidence: 0.95,
        eligible_edge_count: 1,
        candidate_pool_size: 1,
        excluded_missing_context: 0,
        excluded_pathological: 0,
        highlights: [{
          rank: 1,
          proposal_id: relationship.proposal_id,
          source_id: winnerYes.id,
          source_label: winnerYes.plain_claim,
          source_market_id: winnerYes.market_id,
          source_event_key: "wc2026",
          source_domain: "sports",
          source_team_name: "Brazil",
          source_stage_key: "winner",
          source_stage_rank: 5,
          source_plain_claim: winnerYes.plain_claim,
          target_id: finalYes.id,
          target_label: finalYes.plain_claim,
          target_market_id: finalYes.market_id,
          target_event_key: "wc2026",
          target_domain: "sports",
          target_team_name: "Brazil",
          target_stage_key: "final",
          target_stage_rank: 4,
          target_plain_claim: finalYes.plain_claim,
          template_key: "winner:yes->final:yes",
          component_id: "brazil-progression",
          relation: "implies",
          confidence: 1,
          evidence_tier: "deterministic_rule",
          discovery_method: "deterministic",
          explanation_excerpt: "Winning requires reaching the final.",
          importance_score: 0.98,
          score_breakdown: scoreBreakdown,
        }],
        graph: graphView,
        context_pruning: { incident_edge_cap_per_endpoint: 2, candidate_nodes: 2, candidate_edges: 1, retained_nodes: 2, retained_edges: 1, pruned_nodes: 0, pruned_edges: 0 },
      };
    } else if (url.pathname.endsWith("/search")) {
      const query = (url.searchParams.get("q") ?? "").toLowerCase();
      body = query.includes("not")
        ? [{
            node_id: finalNo.id,
            market_id: finalNo.market_id,
            outcome_label: "No",
            event_slug: "wc2026",
            canonical_proposition: finalNo.technical_canonical_label,
            plain_claim: finalNo.plain_claim,
          }]
        : [{
            node_id: winnerYes.id,
            market_id: winnerYes.market_id,
            outcome_label: "Yes",
            event_slug: "wc2026",
            canonical_proposition: "Will Brazil win the 2026 FIFA World Cup?",
            plain_claim: winnerYes.plain_claim,
          }];
    } else if (url.pathname.endsWith("/subgraph")) {
      body = graphView;
    } else if (url.pathname.includes("/nodes/")) {
      body = { node: graphNodes[0], edges: [graphEdge] };
    } else if (url.pathname.endsWith("/prove")) {
      body = [{ from_node_id: winnerYes.id, to_node_id: finalYes.id, steps: [], hops: 1, bottleneck_confidence: 1 }];
    } else if (url.pathname.endsWith("/why-not")) {
      body = { status: "not_retrieved", explanation: "Pair was not retrieved" };
    } else {
      body = {};
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
});

test("starts with a human World Cup progression map, not a graph hairball", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Follow how team outcomes connect" })).toBeVisible();
  await expect(page.getByLabel("Map scope").getByText("Model review not needed")).toBeVisible();
  await expect(page.getByRole("link", { name: /Final 1 team/ })).toBeVisible();
  await expect(page.getByText("If Brazil wins the World Cup, then Brazil reaches the final.")).toBeVisible();
  await expect(page.locator(".graph-canvas")).toHaveCount(0);
  await expect(page.getByText(/Exact Score/)).toHaveCount(0);
  await expect(page.getByText(/NOT\(/)).not.toBeVisible();
});

test("navigates stage, team, and binary market details with human context", async ({ page }) => {
  await page.goto("/#/explore/stage/final");
  await expect(page.getByRole("heading", { name: "Final", exact: true })).toBeVisible();
  await page.getByRole("link", { name: /Brazil 2 progression markets/ }).click();
  await expect(page.getByRole("heading", { name: "Brazil", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Progression ladder" })).toBeVisible();
  await page.getByRole("link", { name: "View this market" }).last().click();
  await expect(page.getByRole("heading", { name: winnerYes.question })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Exactly one answer can be true" })).toBeVisible();
  await expect(page.getByText("Brazil does not win the World Cup")).toBeVisible();
  await expect(page.getByText(/NOT\(/)).not.toBeVisible();
});

test("compares two outcomes and explains the direct connection", async ({ page }) => {
  await page.goto("/#/compare");
  const first = page.getByText("First outcome").locator("..");
  await first.getByPlaceholder("Team and stage").fill("wins");
  await first.getByRole("button", { name: "Find" }).click();
  await first.getByRole("button", { name: /Brazil wins the World Cup/ }).click();
  const second = page.getByText("Second outcome").locator("..");
  await second.getByPlaceholder("Team and stage").fill("final");
  await second.getByRole("button", { name: "Find" }).click();
  await second.getByRole("button", { name: /Brazil reaches the final/ }).click();
  await page.getByRole("button", { name: "Compare outcomes" }).click();
  await expect(page.getByText("Direct connection")).toBeVisible();
  await expect(page.getByText("If Brazil wins the World Cup, then Brazil reaches the final.")).toBeVisible();
});

test("keeps raw relationship rule identifiers inside Technical details", async ({ page }) => {
  await page.goto(`/#/explore/relationship/${relationship.proposal_id}`);
  await expect(page.getByRole("heading", { name: "Why this connection exists" })).toBeVisible();
  await expect(page.getByText(relationship.explanation)).toBeVisible();
  const rawBasis = page.getByText(relationship.basis, { exact: true });
  await expect(rawBasis).not.toBeVisible();
  await page.locator(".technical-page-details > summary").click();
  await expect(rawBasis).toBeVisible();
});

test("opens only the graph and its controls with cross-team logic selected", async ({ page }) => {
  await page.goto("/#/analyst");
  await expect(page.locator(".topbar, .primary-nav, .site-footer")).toHaveCount(0);
  await expect(page.locator(".graph-canvas")).toBeVisible();
  await expect(page.getByLabel("Show")).toHaveValue("all");
  await expect(page.getByLabel("Grouping")).toHaveCount(0);
  await expect(page.getByRole("option", { name: "Can coexist" })).toHaveCount(0);
  await expect(page.getByText("Earlier market close")).toBeVisible();
  await expect(page.getByText("Proof tools")).toHaveCount(0);
  await page.getByLabel("Find a team or outcome").fill("Brazil");
  await page.getByRole("button", { name: "Find" }).click();
  await page.getByRole("button", { name: /Brazil wins the World Cup/ }).click();
  await expect(page.getByRole("heading", { name: "Brazil wins the World Cup" })).toBeVisible();
  await expect(page.getByText(`ID: ${winnerYes.id}`)).not.toBeVisible();
  await page.getByText("Technical details").click();
  await expect(page.getByText(`ID: ${winnerYes.id}`)).toBeVisible();
  await page.getByLabel("Find a team or outcome").fill("not");
  await page.getByRole("button", { name: "Find" }).click();
  await expect(page.getByRole("button", { name: finalNo.plain_claim })).toBeVisible();
  await expect(page.getByText(/NOT\(/)).toHaveCount(0);
  await page.getByText("Proof tools").click();
  await expect(page.getByLabel("From outcome ID")).toBeVisible();
});

test("records a human-captioned, hairball-free deterministic story", async ({ page }) => {
  await page.goto("/#/analyst");
  await page.getByText("More").click();
  await page.getByRole("button", { name: "Auto story" }).click();
  await expect(page.getByRole("heading", { name: "FIFA World Cup 2026 Outcome Map" })).toBeVisible();
  await expect(page.locator(".presentation-shell")).toHaveClass(/presentation-intro/);
  await page.waitForFunction(() => window.__ODDSFOX_RECORDING__?.ready === true);
  expect(await page.evaluate(() => window.__ODDSFOX_RECORDING__?.getFrameCount())).toBe(390);
  await page.evaluate(() => window.__ODDSFOX_RECORDING__?.seek(180));
  await expect(page.getByRole("heading", { name: "If Brazil wins the World Cup, then Brazil reaches the final." })).toBeVisible();
  await expect(page.getByText("Proven by a logic rule")).toBeVisible();
  await page.evaluate(() => window.__ODDSFOX_RECORDING__?.seek(389));
  await expect(page.locator(".presentation-shell")).toHaveClass(/presentation-outro/);
  await expect(page.getByText(/nodes ·/)).toHaveCount(0);
});

test("supports keyboard search and browser back navigation", async ({ page }) => {
  await page.goto("/");
  const search = page.getByRole("search").first();
  await search.getByRole("textbox").focus();
  await page.keyboard.type("Brazil");
  await page.keyboard.press("Enter");
  const result = page.locator(".entity-results").getByRole("link", { name: "Brazil 2 progression markets", exact: true });
  await result.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Brazil", exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Follow how team outcomes connect" })).toBeVisible();
});

test("hides live-only tools when metadata declares a static snapshot", async ({ page }) => {
  await page.route("**/api/v1/meta", async (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      package_version: "0.12.0",
      viewer: { static: true, build_mode: "fast", validation_status: "DETERMINISTIC_VALIDATED", graph_content_fingerprint: "fixture" },
      coverage: { classification_status: "not_applicable", classification_coverage: null },
      build: { static: true, build_mode: "fast", validation_status: "DETERMINISTIC_VALIDATED" },
    }),
  }));
  await page.goto("/#/analyst");
  await expect(page.locator(".topbar, .primary-nav, .site-footer")).toHaveCount(0);
  await expect(page.getByText("Proof tools")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Auto story" })).toHaveCount(0);
});

test("reflows at 390px and honors dark reduced-motion preferences", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Follow how team outcomes connect" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await page.locator(".stage-map").evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(" ").length)).toBe(1);
  expect(await page.locator("html").evaluate((element) => getComputedStyle(element).scrollBehavior)).toBe("auto");
  expect(await page.locator("body").evaluate((element) => getComputedStyle(element).backgroundColor)).toBe("rgb(13, 18, 25)");
  await page.goto("/#/analyst");
  await expect(page.locator(".graph-canvas")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

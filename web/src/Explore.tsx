import { FormEvent, ReactNode, useEffect, useId, useState } from "react";
import * as api from "./api";
import {
  countLabel,
  coverageLabel,
  evidenceLabel,
  marketsByProgressionLevel,
  progressionLabel,
  relationLabel,
  relationshipSentence,
  stageLabel,
} from "./human";
import type {
  ClaimSummary,
  CompareResult,
  EntitySearchResult,
  MarketDetail,
  RelationshipDetail,
  TeamDetail,
} from "./types";

export type ExploreRoute =
  | { kind: "home" }
  | { kind: "stage"; id: string }
  | { kind: "team"; id: string }
  | { kind: "market"; id: string }
  | { kind: "relationship"; id: string }
  | { kind: "compare"; initialClaimId?: string };

interface Props {
  route: ExploreRoute;
}

export function Explore({ route }: Props) {
  switch (route.kind) {
    case "stage":
      return <StagePage stageKey={route.id} />;
    case "team":
      return <TeamPage teamKey={route.id} />;
    case "market":
      return <MarketPage marketId={route.id} />;
    case "relationship":
      return <RelationshipPage proposalId={route.id} />;
    case "compare":
      return <ComparePage initialClaimId={route.initialClaimId} />;
    case "home":
      return <ExploreHomePage />;
  }
}

function ExploreHomePage() {
  const resource = useResource(() => api.exploreHome(), []);
  if (resource.pending) return <PageLoading label="Loading World Cup outcomes…" />;
  if (resource.error) return <PageError error={resource.error} />;
  const home = resource.value!;
  const coverage = coverageLabel(
    String(home.coverage.classification_status ?? ""),
    home.coverage.classification_coverage,
  );
  return (
    <div className="explore-page explore-home">
      <section className="hero" aria-labelledby="explore-heading">
        <div>
          <p className="eyebrow">World Cup 2026 · Polymarket</p>
          <h2 id="explore-heading">Follow how team outcomes connect</h2>
          <p className="hero-copy">Explore the logic between team progression markets—from reaching the round of 32 to becoming world champion.</p>
        </div>
        <aside className="scope-card" aria-label="Map scope">
          <strong>{countLabel(home.scope.team_count, "team")}</strong>
          <span>{countLabel(home.scope.market_count, "progression market")}</span>
          <span>{coverage}</span>
          <small>This map excludes score, player, corner, and match-minute markets.</small>
        </aside>
      </section>

      <section className="task-strip" aria-label="Start exploring">
        <EntitySearch />
        <a className="task-card" href="#/compare">
          <span>Compare two outcomes</span>
          <small>See whether one requires, excludes, or matches the other.</small>
        </a>
      </section>

      <section className="content-section" aria-labelledby="stages-heading">
        <SectionHeading
          eyebrow="Tournament path"
          title="Six steps from knockout stage to champion"
          id="stages-heading"
        />
        <ol className="stage-map">
          {home.stages.map((stage, index) => (
            <li key={stage.stage_key}>
              <a href={`#/explore/stage/${encodeURIComponent(stage.stage_key)}`}>
                <span className="stage-number" aria-hidden="true">{index + 1}</span>
                <strong>{stage.label}</strong>
                <small>{countLabel(stage.team_count, "team")} · {countLabel(stage.market_count, "market")}</small>
                <small className="coverage-copy">{coverageLabel(stage.classification_status, stage.classification_coverage)}</small>
              </a>
            </li>
          ))}
        </ol>
      </section>

      {home.relationship_groups.length > 0 && (
        <section className="content-section" aria-labelledby="rules-heading">
          <SectionHeading eyebrow="Tournament rules" title="Repeated logic, grouped clearly" id="rules-heading" />
          <div className="card-grid compact-grid">
            {home.relationship_groups.map((group) => (
              <article className="rule-card" key={group.id}>
                <span className="relation-symbol" data-relation={group.relation} aria-hidden="true" />
                <div>
                  <h3>{group.title}</h3>
                  <p>{group.description}</p>
                  <small>{countLabel(group.relationship_count, "individual connection")} summarized</small>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      <section className="content-section" aria-labelledby="connections-heading">
        <SectionHeading eyebrow="Notable connections" title="Important relationships in plain language" id="connections-heading" />
        {home.notable_relationships.length > 0 ? (
          <div className="relationship-grid">
            {home.notable_relationships.map((highlight) => (
              <RelationshipCard key={highlight.relationship.proposal_id} relationship={highlight.relationship} />
            ))}
          </div>
        ) : <EmptyState>No notable relationships meet the current graph’s validation threshold.</EmptyState>}
      </section>

      <section className="content-section" aria-labelledby="teams-heading">
        <SectionHeading eyebrow="Teams" title="Choose a team and follow its progression ladder" id="teams-heading" />
        <div className="team-grid">
          {home.teams.map((team) => (
            <a className="team-card" key={team.team_key} href={`#/explore/team/${encodeURIComponent(team.team_key)}`}>
              <span className={`status-dot ${team.is_still_alive === false ? "status-out" : ""}`} aria-hidden="true" />
              <strong>{team.canonical_team_name}</strong>
              <small>{countLabel(team.market_count, "progression market")} · through {stageLabel(team.stage_keys.at(-1) ?? "winner")}</small>
              <small className="coverage-copy">{coverageLabel(team.classification_status, team.classification_coverage)}</small>
            </a>
          ))}
        </div>
      </section>
    </div>
  );
}

function StagePage({ stageKey }: { stageKey: string }) {
  const resource = useResource(() => api.stageDetail(stageKey), [stageKey]);
  if (resource.pending) return <PageLoading label="Loading stage…" />;
  if (resource.error) return <PageError error={resource.error} />;
  const { summary, teams, markets } = resource.value!;
  return (
    <DetailPage
      crumbs={[{ label: "Outcome map", href: "#/explore" }, { label: summary.label }]}
      eyebrow="Tournament stage"
      title={summary.label}
      description={`${countLabel(summary.team_count, "team")} represented · ${countLabel(summary.market_count, "progression market")}. ${coverageLabel(summary.classification_status, summary.classification_coverage)}.`}
    >
      <EntitySearch label={`Search ${summary.label.toLocaleLowerCase()} teams and outcomes`} />
      <section className="content-section" aria-labelledby="stage-teams-heading">
        <SectionHeading eyebrow="Teams represented" title="Choose a team" id="stage-teams-heading" />
        <div className="team-grid">
          {teams.map((team) => (
            <a className="team-card" href={`#/explore/team/${encodeURIComponent(team.team_key)}`} key={team.team_key}>
              <strong>{team.canonical_team_name}</strong>
              <small>{countLabel(team.market_count, "progression market")}</small>
              <small className="coverage-copy">{coverageLabel(team.classification_status, team.classification_coverage)}</small>
            </a>
          ))}
        </div>
      </section>
      <section className="content-section" aria-labelledby="stage-markets-heading">
        <SectionHeading eyebrow="Binary markets" title="Yes and No shown together" id="stage-markets-heading" />
        <div className="market-grid">
          {markets.map((market) => <MarketCard key={market.market_id} market={market} />)}
        </div>
      </section>
    </DetailPage>
  );
}

function TeamPage({ teamKey }: { teamKey: string }) {
  const resource = useResource(() => api.teamDetail(teamKey), [teamKey]);
  if (resource.pending) return <PageLoading label="Loading team progression…" />;
  if (resource.error) return <PageError error={resource.error} />;
  const detail = resource.value!;
  return (
    <DetailPage
      crumbs={[{ label: "Outcome map", href: "#/explore" }, { label: detail.summary.canonical_team_name }]}
      eyebrow="Team progression"
      title={detail.summary.canonical_team_name}
      description={`Each step groups the market’s Yes and No outcomes so the progression path stays readable. ${coverageLabel(detail.summary.classification_status, detail.summary.classification_coverage)}.`}
    >
      <section className="content-section" aria-labelledby="ladder-heading">
        <SectionHeading eyebrow="Knockout path" title="Progression ladder" id="ladder-heading" />
        <TeamLadder detail={detail} />
      </section>
    </DetailPage>
  );
}

function MarketPage({ marketId }: { marketId: string }) {
  const resource = useResource(() => api.marketDetail(marketId), [marketId]);
  if (resource.pending) return <PageLoading label="Loading market…" />;
  if (resource.error) return <PageError error={resource.error} />;
  const market = resource.value!;
  return (
    <DetailPage
      crumbs={[
        { label: "Outcome map", href: "#/explore" },
        { label: market.canonical_team_name, href: `#/explore/team/${encodeURIComponent(teamKey(market.canonical_team_name))}` },
        { label: progressionLabel(market.normalized_progression_level) },
      ]}
      eyebrow="Polymarket progression market"
      title={market.question}
      description={`${market.canonical_team_name} · ${progressionLabel(market.normalized_progression_level)} · ${market.market_status}`}
    >
      <section className="content-section" aria-labelledby="answers-heading">
        <SectionHeading eyebrow="Two possible answers" title="Exactly one answer can be true" id="answers-heading" />
        <div className="claim-pair">
          {market.claims.map((claim) => <ClaimCard key={claim.id} claim={claim} />)}
        </div>
      </section>
      <details className="technical-details technical-page-details">
        <summary>Technical details</summary>
        <dl>
          <div><dt>Market ID</dt><dd>{market.market_id}</dd></div>
          <div><dt>Event slug</dt><dd>{market.event_slug}</dd></div>
          <div><dt>Direction</dt><dd>{market.market_direction}</dd></div>
        </dl>
      </details>
    </DetailPage>
  );
}

function RelationshipPage({ proposalId }: { proposalId: string }) {
  const resource = useResource(() => api.relationshipDetail(proposalId), [proposalId]);
  if (resource.pending) return <PageLoading label="Loading connection…" />;
  if (resource.error) return <PageError error={resource.error} />;
  const relationship = resource.value!;
  return (
    <DetailPage
      crumbs={[{ label: "Outcome map", href: "#/explore" }, { label: "Logical connection" }]}
      eyebrow="Verified relationship"
      title={relationLabel(relationship.relation)}
      description={relationshipSentence(relationship)}
    >
      <section className="relationship-detail" aria-label="Connected outcomes">
        <ClaimCard claim={relationship.source} label="First outcome" />
        <div className="relationship-connector" data-relation={relationship.relation}>
          <span aria-hidden="true" />
          <strong>{relationLabel(relationship.relation)}</strong>
        </div>
        <ClaimCard claim={relationship.target} label="Second outcome" />
      </section>
      <section className="why-card" aria-labelledby="why-heading">
        <p className="eyebrow">{evidenceLabel(relationship.evidence_tier)}</p>
        <h3 id="why-heading">Why this connection exists</h3>
        <p>{relationship.explanation}</p>
      </section>
      <details className="technical-details technical-page-details">
        <summary>Technical details</summary>
        <dl>
          <div><dt>Relationship ID</dt><dd>{relationship.proposal_id}</dd></div>
          <div><dt>Confidence</dt><dd>{(relationship.confidence * 100).toFixed(2)}%</dd></div>
          <div><dt>Discovery method</dt><dd>{relationship.discovery_method}</dd></div>
          <div><dt>Rule basis</dt><dd>{relationship.basis}</dd></div>
        </dl>
      </details>
    </DetailPage>
  );
}

function ComparePage({ initialClaimId }: { initialClaimId?: string }) {
  const [source, setSource] = useState<EntitySearchResult | null>(null);
  const [target, setTarget] = useState<EntitySearchResult | null>(null);
  const [result, setResult] = useState<CompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!initialClaimId) return;
    let active = true;
    void api.compare(initialClaimId, initialClaimId)
      .then((same) => {
        if (active) setSource({
          kind: "claim",
          id: same.source.id,
          label: same.source.plain_claim,
          description: `${same.source.answer} outcome`,
        });
      })
      .catch((reason: unknown) => {
        if (active) setError(message(reason));
      });
    return () => { active = false; };
  }, [initialClaimId]);

  async function compare() {
    if (!source || !target) return;
    setPending(true);
    setError(null);
    try {
      setResult(await api.compare(source.id, target.id));
    } catch (reason) {
      setError(message(reason));
    } finally {
      setPending(false);
    }
  }

  return (
    <DetailPage
      crumbs={[{ label: "Outcome map", href: "#/explore" }, { label: "Compare outcomes" }]}
      eyebrow="Outcome comparison"
      title="How do these outcomes relate?"
      description="Choose two team outcomes. The map checks a direct relationship first, then one short supported logic path."
    >
      <section className="compare-form" aria-label="Choose two outcomes">
        <OutcomePicker label="First outcome" value={source} onChange={(claim) => { setSource(claim); setResult(null); }} />
        <span className="compare-and" aria-hidden="true">and</span>
        <OutcomePicker label="Second outcome" value={target} onChange={(claim) => { setTarget(claim); setResult(null); }} />
        <button type="button" disabled={!source || !target || source.id === target.id || pending} onClick={() => void compare()}>
          {pending ? "Checking…" : "Compare outcomes"}
        </button>
      </section>
      {error && <div className="error inline-error" role="alert">{error}</div>}
      {result && <CompareResultView result={result} />}
    </DetailPage>
  );
}

function CompareResultView({ result }: { result: CompareResult }) {
  if (result.status === "no_proven_relationship") {
    return (
      <section className="compare-result empty-compare" aria-live="polite">
        <p className="eyebrow">No supported connection</p>
        <h3>These outcomes are not linked by the published logic</h3>
        <p>{result.explanation}</p>
        <small>This does not mean the outcomes are unrelated in every real-world sense—only that this graph did not infer a valid relationship.</small>
      </section>
    );
  }
  const relationships = result.direct ? [result.direct] : result.path;
  return (
    <section className="compare-result" aria-live="polite">
      <p className="eyebrow">{result.direct ? "Direct connection" : `${result.path.length}-step logic path`}</p>
      <h3>{result.explanation}</h3>
      <ol className="compare-path">
        {relationships.map((relationship) => (
          <li key={relationship.proposal_id}>
            <RelationshipCard relationship={relationship} />
          </li>
        ))}
      </ol>
    </section>
  );
}

function EntitySearch({ label = "Search teams, stages, markets, and outcomes" }: { label?: string }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<EntitySearchResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const listId = useId();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setError(null);
    try {
      setResults(await api.entitySearch(query.trim()));
    } catch (reason) {
      setError(message(reason));
    }
  }

  return (
    <form className="entity-search" role="search" onSubmit={submit}>
      <label htmlFor={`${listId}-input`}>{label}</label>
      <div>
        <input id={`${listId}-input`} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Try Brazil, winner, or reach the final" aria-controls={listId} />
        <button type="submit">Search</button>
      </div>
      {error && <span className="field-error" role="alert">{error}</span>}
      {results.length > 0 && (
        <ul className="entity-results" id={listId} aria-label="Search results">
          {results.map((result) => (
            <li key={`${result.kind}-${result.id}`}>
              <a href={entityRoute(result)}>
                <span>{result.label}</span>
                <small>{result.description}</small>
              </a>
            </li>
          ))}
        </ul>
      )}
    </form>
  );
}

function OutcomePicker({
  label,
  value,
  onChange,
}: {
  label: string;
  value: EntitySearchResult | null;
  onChange: (claim: EntitySearchResult | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<EntitySearchResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setError(null);
    try {
      const matches = await api.entitySearch(query.trim(), 20);
      setResults(matches.filter((item) => item.kind === "claim"));
    } catch (reason) {
      setError(message(reason));
    }
  }

  return (
    <div className="outcome-picker">
      <strong>{label}</strong>
      {value ? (
        <div className="picked-outcome">
          <span>{value.label}</span>
          <small>{value.description}</small>
          <button type="button" className="text-button" onClick={() => { onChange(null); setQuery(""); setResults([]); }}>Choose another</button>
        </div>
      ) : (
        <form onSubmit={submit} role="search">
          <label className="sr-only" htmlFor={`${id}-input`}>Search {label.toLocaleLowerCase()}</label>
          <div>
            <input id={`${id}-input`} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Team and stage" />
            <button type="submit">Find</button>
          </div>
          {error && <span className="field-error" role="alert">{error}</span>}
          {results.length > 0 && (
            <ul className="picker-results">
              {results.map((result) => (
                <li key={result.id}>
                  <button type="button" onClick={() => { onChange(result); setResults([]); }}>
                    <span>{result.label}</span>
                    <small>{result.description}</small>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </form>
      )}
    </div>
  );
}

function TeamLadder({ detail }: { detail: TeamDetail }) {
  const groupedMarkets = marketsByProgressionLevel(detail.markets);
  return (
    <ol className="team-ladder">
      {groupedMarkets.map((markets, index) => {
        return (
          <li key={index} className={markets.length > 0 ? "has-market" : "missing-market"}>
            <span className="ladder-marker" aria-hidden="true">{index + 1}</span>
            <div>
              <h3>{progressionLabel(index)}</h3>
              {markets.length > 0
                ? <div className="ladder-markets">{markets.map((market) => <MarketCard key={market.market_id} market={market} />)}</div>
                : <p>No progression market is present for this stage.</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function MarketCard({ market }: { market: MarketDetail }) {
  return (
    <article className="market-card">
      <div className="market-card-heading">
        <div>
          <p className="eyebrow">Outcome: {progressionLabel(market.normalized_progression_level)}</p>
          <h3>{market.question}</h3>
        </div>
        <span className={`status-pill status-${market.market_status}`}>{market.market_status}</span>
      </div>
      <div className="binary-answers" aria-label="Market answers">
        {market.claims.map((claim) => (
          <span key={claim.id} className={claim.is_progression_token ? "progression-answer" : "opposite-answer"}>
            <strong>{claim.answer}</strong>
            <small>{claim.plain_claim}</small>
          </span>
        ))}
      </div>
      <a className="text-link" href={`#/explore/market/${encodeURIComponent(market.market_id)}`}>View this market</a>
    </article>
  );
}

function ClaimCard({ claim, label }: { claim: ClaimSummary; label?: string }) {
  return (
    <article className="claim-card">
      {label && <p className="eyebrow">{label}</p>}
      <div className="claim-context">
        <span>{claim.canonical_team_name}</span>
        <span>{progressionLabel(claim.normalized_progression_level)}</span>
        <span>{claim.answer}</span>
      </div>
      <h3>{claim.plain_claim}</h3>
      <p>{claim.question}</p>
      <details className="technical-details">
        <summary>Technical details</summary>
        <dl>
          <div><dt>Claim ID</dt><dd>{claim.id}</dd></div>
          <div><dt>Canonical label</dt><dd>{claim.technical_canonical_label}</dd></div>
          <div><dt>Market status</dt><dd>{claim.market_status}</dd></div>
        </dl>
      </details>
    </article>
  );
}

function RelationshipCard({ relationship }: { relationship: RelationshipDetail }) {
  return (
    <article className="relationship-card">
      <span className="relation-symbol" data-relation={relationship.relation} aria-hidden="true" />
      <div>
        <p className="eyebrow">{relationLabel(relationship.relation)}</p>
        <h3>{relationshipSentence(relationship)}</h3>
        <p>{evidenceLabel(relationship.evidence_tier)}</p>
        <a className="text-link" href={`#/explore/relationship/${encodeURIComponent(relationship.proposal_id)}`}>Understand this connection</a>
      </div>
    </article>
  );
}

function DetailPage({
  crumbs,
  eyebrow,
  title,
  description,
  children,
}: {
  crumbs: Array<{ label: string; href?: string }>;
  eyebrow: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="explore-page detail-page">
      <nav className="page-breadcrumbs" aria-label="Page location">
        {crumbs.map((crumb) => crumb.href
          ? <a key={crumb.label} href={crumb.href}>{crumb.label}</a>
          : <span key={crumb.label} aria-current="page">{crumb.label}</span>)}
      </nav>
      <header className="detail-heading">
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
        <p>{description}</p>
      </header>
      {children}
    </div>
  );
}

function SectionHeading({ eyebrow, title, id }: { eyebrow: string; title: string; id: string }) {
  return (
    <header className="section-heading">
      <p className="eyebrow">{eyebrow}</p>
      <h2 id={id}>{title}</h2>
    </header>
  );
}

function PageLoading({ label }: { label: string }) {
  return <div className="page-state" role="status"><span className="state-spinner" aria-hidden="true" />{label}</div>;
}

function PageError({ error }: { error: string }) {
  return (
    <div className="page-state error-state" role="alert">
      <h2>We couldn’t load this part of the outcome map</h2>
      <p>{error}</p>
      <a href="#/explore">Return to the outcome map</a>
    </div>
  );
}

function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

function entityRoute(result: EntitySearchResult): string {
  if (result.kind === "team") return `#/explore/team/${encodeURIComponent(result.id)}`;
  if (result.kind === "stage") return `#/explore/stage/${encodeURIComponent(result.id)}`;
  if (result.kind === "claim") return `#/compare?claim=${encodeURIComponent(result.id)}`;
  return `#/explore/market/${encodeURIComponent(result.id)}`;
}

function teamKey(name: string): string {
  return name.toLocaleLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function useResource<T>(load: () => Promise<T>, dependencies: unknown[]) {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(true);
  useEffect(() => {
    let active = true;
    setPending(true);
    setError(null);
    void load()
      .then((next) => {
        if (active) setValue(next);
      })
      .catch((reason: unknown) => {
        if (active) setError(message(reason));
      })
      .finally(() => {
        if (active) setPending(false);
      });
    return () => { active = false; };
    // The caller supplies primitive route dependencies; load intentionally follows them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);
  return { value, error, pending };
}

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

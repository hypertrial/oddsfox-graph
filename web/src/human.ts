import type {
  ClaimSummary,
  ClassificationStatus,
  MarketDetail,
  RecordingHighlight,
  Relation,
  RelationshipDetail,
} from "./types";

const stageLabels: Record<string, string> = {
  round_of_32: "Round of 32",
  round_of_16: "Round of 16",
  quarterfinal: "Quarterfinals",
  semifinal: "Semifinals",
  final: "Final",
  winner: "Champion",
};

const progressionLabels = [
  "Round of 32",
  "Round of 16",
  "Quarterfinals",
  "Semifinals",
  "Final",
  "World Cup winner",
] as const;

export function stageLabel(stageKey: string): string {
  return stageLabels[stageKey] ?? titleCase(stageKey);
}

export function progressionLabel(level: number): string {
  return progressionLabels[level] ?? `Progression level ${level}`;
}

export function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function marketsByProgressionLevel(markets: MarketDetail[]): MarketDetail[][] {
  return progressionLabels.map((_, level) => markets.filter(
    (market) => market.normalized_progression_level === level,
  ));
}

export function marketsForProgressionStage(
  markets: MarketDetail[],
  normalizedProgressionLevel: number,
): MarketDetail[] {
  return markets.filter(
    (market) => market.normalized_progression_level === normalizedProgressionLevel,
  );
}

export function relationLabel(relation: Relation): string {
  switch (relation) {
    case "implies":
      return "If this happens, that must have happened";
    case "equivalent":
      return "These mean the same thing";
    case "complement":
      return "Exactly one answer is true";
    case "mutually_exclusive":
      return "These cannot both happen";
    case "compatible":
      return "These can both happen";
  }
}

export function relationshipSentence(relationship: RelationshipDetail): string {
  return relationshipSentenceFromParts(
    relationship.source.plain_claim,
    relationship.relation,
    relationship.target.plain_claim,
  );
}

export function relationshipSentenceFromParts(
  rawSource: string,
  relation: Relation,
  rawTarget: string,
): string {
  const source = safeClaim(rawSource);
  const target = safeClaim(rawTarget);
  switch (relation) {
    case "implies":
      return `If ${source}, then ${target}.`;
    case "equivalent":
      return `${source} and ${target} describe the same tournament outcome.`;
    case "complement":
      return `Exactly one is true: ${source} or ${target}.`;
    case "mutually_exclusive":
      return `${source} and ${target} cannot both happen.`;
    case "compatible":
      return `${source} and ${target} can both happen.`;
  }
}

export function highlightSentence(highlight: RecordingHighlight): string {
  return relationshipSentenceFromParts(
    highlight.source_plain_claim,
    highlight.relation,
    highlight.target_plain_claim,
  );
}

export function evidenceLabel(tier: string): string {
  switch (tier) {
    case "source_contract":
      return "Defined by the market";
    case "deterministic_rule":
      return "Proven by a logic rule";
    case "generative_consensus":
      return "Supported by independent model checks";
    default:
      return "Evidence unavailable";
  }
}

export function coverageLabel(
  status: ClassificationStatus | string | undefined,
  coverage: unknown,
): string {
  if (status === "not_applicable") return "Model review not needed";
  if (status === "not_started") return "Model review not started";
  if (status === "complete") return "Model review complete";
  if (status === "partial") return "Model review partially complete";
  if (typeof coverage === "number") {
    return coverage >= 1 ? "Model review complete" : "Model review partially complete";
  }
  return "Model review status unavailable";
}

export function validationLabel(status: string, mode?: string): string {
  const normalized = status.trim().toLocaleUpperCase();
  if (normalized === "DETERMINISTIC_VALIDATED") return "Logic rules verified";
  if (normalized === "EXPERIMENTAL_FULL" || normalized.includes("GENERATIVE")) return "Model-reviewed graph";
  if (normalized.includes("VALID")) return mode === "full" ? "Model-reviewed graph" : "Graph verified";
  return "Graph ready";
}

export function claimDescription(claim: ClaimSummary): string {
  return `${claim.canonical_team_name} · ${progressionLabel(claim.normalized_progression_level)} · ${claim.answer}`;
}

export function safeClaim(value: string): string {
  const trimmed = value.trim().replace(/\s+/g, " ");
  const negated = /^NOT\((.*)\)$/i.exec(trimmed);
  if (negated) return `It is not true that ${negated[1].trim()}`;
  return trimmed || "This outcome";
}

function titleCase(value: string): string {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

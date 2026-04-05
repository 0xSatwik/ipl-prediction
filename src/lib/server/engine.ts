import {
	type AppData,
	type FantasyResult,
	type GlobalPriors,
	type HeadToHead,
	type MatchFactor,
	type ModelWeights,
	type PairStat,
	type PlayerProfile,
	type PredictionInput,
	type PredictionResult,
	type Role,
	type ScoreDistribution,
	type TeamProfile,
	type VenueProfile,
	type XiPlayer
} from '$lib/types';

import { findTeam, findVenue } from '$lib/server/data';

/* ─── constants ─── */

const ROLE_MINIMUMS: Record<Role, number> = {
	Wicketkeeper: 1,
	Batter: 3,
	'All-Rounder': 1,
	Bowler: 3
};

const ROLE_MAXIMUMS: Record<Role, number> = {
	Wicketkeeper: 4,
	Batter: 6,
	'All-Rounder': 4,
	Bowler: 6
};

const MAX_OVERSEAS = 4;
const MONTE_CARLO_ITERATIONS = 8000;
const DEFAULT_STRATEGY = 'safe' as const;

/* ─── small helpers ─── */

function clamp(value: number, min: number, max: number) {
	return Math.max(min, Math.min(max, value));
}

function logistic(value: number) {
	return 1 / (1 + Math.exp(-value));
}

function round(value: number, digits = 2) {
	return Number(value.toFixed(digits));
}

function average(values: number[]) {
	if (!values.length) return 0;
	return values.reduce((s, v) => s + v, 0) / values.length;
}

function strategyOf(input: PredictionInput) {
	return input.strategy ?? DEFAULT_STRATEGY;
}

/** Simple seeded PRNG (xorshift32) for reproducible Monte Carlo */
function makeRng(seed = 42) {
	let s = seed | 0;
	return () => {
		s ^= s << 13;
		s ^= s >> 17;
		s ^= s << 5;
		return (s >>> 0) / 4294967296;
	};
}

/** Box-Muller transform: generate a sample from N(mean, std) */
function normalSample(rng: () => number, mean: number, std: number) {
	const u1 = rng() || 1e-10;
	const u2 = rng();
	return mean + std * Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

/* ─── venue / pair / H2H lookups ─── */

function getVenueBoost(player: PlayerProfile, venue: string) {
	const hit = player.venueHighlights.find((v) => v.venue === venue);
	return hit ?? {
		battingScore: player.batting.index * 0.7,
		bowlingScore: player.bowling.index * 0.7,
		sample: 0
	};
}

function getScenarioBoost(player: PlayerProfile, battingFirst: boolean) {
	return battingFirst ? player.batting.batFirstIndex : player.batting.chaseIndex;
}

function getHeadToHeadRecord(rows: HeadToHead[], teamA: string, teamB: string) {
	return rows.find(
		(r) =>
			(r.teamA === teamA && r.teamB === teamB) ||
			(r.teamA === teamB && r.teamB === teamA)
	);
}

function teamVenueWinRate(team: TeamProfile, venue: string) {
	return team.venueStats.find((v) => v.venue === venue)?.winRate ?? team.winRate;
}

function buildPairLookup(pairStats: PairStat[], venue: string) {
	const lookup = new Map<string, PairStat>();
	for (const item of pairStats) {
		if (item.scope === 'overall') {
			lookup.set(`${item.batterKey}|${item.bowlerKey}`, item);
		}
	}
	for (const item of pairStats) {
		if (item.scope === 'venue' && item.venue === venue) {
			lookup.set(`${item.batterKey}|${item.bowlerKey}`, item);
		}
	}
	return lookup;
}

/* ─── dew factor ─── */

function dewMultiplier(matchTime: 'afternoon' | 'evening' | undefined, venueChaseWinRate: number) {
	if (!matchTime || matchTime === 'afternoon') return 0;
	// Evening matches: dew makes chasing easier. Scale the advantage by how much
	// the venue already favors chasing (normalize around 50%)
	const chasingBias = (venueChaseWinRate - 50) / 100; // e.g. 55% → 0.05
	return 0.4 + chasingBias * 0.8; // base dew effect + venue-amplified
}

/* ─── scoring candidates (player projections) ─── */

function scoreCandidate(
	player: PlayerProfile,
	venue: string,
	battingFirstTeam: string,
	_priors: GlobalPriors | undefined
) {
	const venueBoost = getVenueBoost(player, venue);
	const battingFirst = player.currentTeam === battingFirstTeam;
	const scenarioBat = getScenarioBoost(player, battingFirst);
	const availability = clamp(player.availability, 0.45, 1);

	// Form score from EWMA (already computed in data pipeline, 0-centered bonus)
	const formBonus = (player.formScore ?? 0) * 0.15;

	// Phase-aware batting: weight death batting higher for finishers, powerplay for openers
	const expectedPos = player.expectedBattingPosition ?? 6;
	const ppBatWeight = expectedPos <= 3 ? 0.25 : 0.08;
	const midBatWeight = 0.4;
	const deathBatWeight = expectedPos >= 5 ? 0.30 : 0.12;
	const phaseAwareBattingIndex =
		(player.batting.powerplayIndex ?? player.batting.index) * ppBatWeight +
		(player.batting.middleIndex ?? player.batting.index) * midBatWeight +
		(player.batting.deathIndex ?? player.batting.index) * deathBatWeight;

	// Phase-aware bowling: weight death bowling ~2x for death specialists
	const expectedOvers = player.expectedOversBowled ?? 0;
	const isMainBowler = expectedOvers >= 3;
	const ppBowlWeight = isMainBowler ? 0.22 : 0.15;
	const midBowlWeight = 0.38;
	const deathBowlWeight = isMainBowler ? 0.40 : 0.20;
	const phaseAwareBowlingIndex =
		(player.bowling.powerplayIndex ?? player.bowling.index) * ppBowlWeight +
		(player.bowling.middleIndex ?? player.bowling.index) * midBowlWeight +
		(player.bowling.deathIndex ?? player.bowling.index) * deathBowlWeight;

	// Current season trust
	const currentSeasonTrust =
		player.starts2026 * 1.8 +
		player.recentStarts2026 * 2.2 +
		player.impactMatches2026 * 0.6 +
		player.recencyScore * 10;
	const inactivePenalty = player.starts2026 === 0 && player.impactMatches2026 === 0 ? 18 : 0;

	// Weight by expected balls faced / overs bowled
	const ballsFacedWeight = clamp((player.expectedBallsFaced ?? 20) / 30, 0.4, 1.4);
	const oversBowledWeight = clamp((player.expectedOversBowled ?? 2) / 4, 0.3, 1.5);

	const battingProjection =
		(phaseAwareBattingIndex * 0.45 +
			player.batting.recentIndex * 0.15 +
			venueBoost.battingScore * 0.15 +
			scenarioBat * 0.15 +
			formBonus * 0.10) *
		(0.78 + availability * 0.22) *
		ballsFacedWeight +
		currentSeasonTrust * 0.22 -
		inactivePenalty * 0.2;

	const bowlingProjection =
		(phaseAwareBowlingIndex * 0.50 +
			player.bowling.recentIndex * 0.15 +
			venueBoost.bowlingScore * 0.20 +
			formBonus * 0.15) *
		(0.8 + availability * 0.2) *
		oversBowledWeight +
		currentSeasonTrust * 0.18 -
		inactivePenalty * 0.18;

	const roleWeight =
		player.role === 'Bowler'
			? bowlingProjection * 0.82 + battingProjection * 0.12
			: player.role === 'All-Rounder'
				? battingProjection * 0.48 + bowlingProjection * 0.52
				: battingProjection * 0.78 + bowlingProjection * 0.08;

	const projectedPoints =
		battingProjection * 0.28 +
		bowlingProjection * 0.44 +
		(player.role === 'All-Rounder' ? 10 : 0) +
		(player.role === 'Wicketkeeper' ? 4 : 0) +
		(player.isCaptain ? 3 : 0) +
		availability * 4 +
		currentSeasonTrust * 0.5 -
		inactivePenalty * 0.35;

	const battingFloor =
		player.batting.average * 0.55 +
		player.batting.recentIndex * 0.09 +
		(player.role === 'Wicketkeeper' ? 4 : 0);
	const bowlingFloor =
		Math.max(0, player.bowling.index * 0.18 + player.bowling.dotRate * 0.12 - player.bowling.economy * 0.7) +
		(player.role === 'Bowler' ? 3 : 0);
	const floorPoints =
		projectedPoints * 0.56 +
		battingFloor * 0.24 +
		bowlingFloor * 0.32 +
		availability * 8 +
		Math.min(player.matchesPlayed, 50) * 0.06 +
		currentSeasonTrust * 0.28;
	const ceilingPoints =
		projectedPoints * 1.24 +
		Math.max(battingProjection, bowlingProjection) * 0.16 +
		(player.role === 'All-Rounder' ? 12 : 0) +
		(player.role === 'Bowler' ? 5 : 0);
	const volatility = Math.max(0, ceilingPoints - floorPoints);
	const consistencyScore =
		floorPoints * 0.72 +
		player.selectionScore * 0.18 +
		availability * 12 -
		volatility * 0.12 +
		player.recencyScore * 8 -
		inactivePenalty * 0.42;

	return {
		...player,
		contextScore: round(
			player.selectionScore +
			roleWeight * 0.32 +
			player.baseFantasyPoints * 0.18 +
			consistencyScore * 0.08 +
			formBonus * 2 -
			inactivePenalty
		),
		projectedPoints: round(projectedPoints),
		battingProjection: round(battingProjection),
		bowlingProjection: round(bowlingProjection),
		floorPoints: round(floorPoints),
		ceilingPoints: round(ceilingPoints),
		volatility: round(volatility),
		consistencyScore: round(consistencyScore),
		roleBucket: player.role
	} satisfies XiPlayer;
}

/* ─── XI selection (with constraint validation for manual picks) ─── */

function canAddPlayer(
	player: XiPlayer,
	selected: XiPlayer[],
	roleCounts: Record<Role, number>,
	overseasCount: number
) {
	if (selected.some((s) => s.playerKey === player.playerKey)) return false;
	if (roleCounts[player.roleBucket] >= ROLE_MAXIMUMS[player.roleBucket]) return false;
	if (player.isOverseas && overseasCount >= MAX_OVERSEAS) return false;
	return true;
}

function validateManualXi(players: XiPlayer[]): string | null {
	const roleCounts: Record<Role, number> = { Wicketkeeper: 0, Batter: 0, 'All-Rounder': 0, Bowler: 0 };
	let overseas = 0;
	for (const p of players) {
		roleCounts[p.roleBucket] += 1;
		if (p.isOverseas) overseas += 1;
	}

	if (overseas > MAX_OVERSEAS) {
		return `Too many overseas players (${overseas}). Maximum is ${MAX_OVERSEAS}.`;
	}

	for (const [role, max] of Object.entries(ROLE_MAXIMUMS) as [Role, number][]) {
		if (roleCounts[role] > max) {
			return `Too many ${role}s (${roleCounts[role]}). Maximum is ${max}.`;
		}
	}

	// Only check minimums if full 11 are selected
	if (players.length === 11) {
		for (const [role, min] of Object.entries(ROLE_MINIMUMS) as [Role, number][]) {
			if (roleCounts[role] < min) {
				return `Not enough ${role}s (${roleCounts[role]}). Minimum is ${min}.`;
			}
		}
	}

	return null;
}

function buildProbableXi(
	team: TeamProfile,
	venue: string,
	battingFirstTeam: string,
	selectedKeys: string[] = [],
	priors: GlobalPriors | undefined
) {
	const candidates = team.squad
		.map((p) => scoreCandidate(p, venue, battingFirstTeam, priors))
		.sort((a, b) => b.contextScore - a.contextScore);

	const selectedKeySet = new Set(selectedKeys);
	if (selectedKeySet.size > 11) {
		throw new Error(`Select at most 11 players for ${team.shortName}.`);
	}

	// Manual XI path — validate constraints
	if (selectedKeySet.size > 0) {
		const manualPlayers = candidates.filter((p) => selectedKeySet.has(p.playerKey));
		const validationError = validateManualXi(manualPlayers);
		if (validationError) {
			throw new Error(`${team.shortName} XI: ${validationError}`);
		}

		// If fewer than 11, fill remaining while respecting constraints
		if (manualPlayers.length < 11) {
			const remaining = candidates.filter((p) => !selectedKeySet.has(p.playerKey));
			const roleCounts: Record<Role, number> = { Wicketkeeper: 0, Batter: 0, 'All-Rounder': 0, Bowler: 0 };
			let overseasCount = 0;
			for (const p of manualPlayers) {
				roleCounts[p.roleBucket] += 1;
				if (p.isOverseas) overseasCount += 1;
			}

			const filled = [...manualPlayers];
			// First pass: fill role minimums
			for (const [role, minimum] of Object.entries(ROLE_MINIMUMS) as [Role, number][]) {
				if (roleCounts[role] >= minimum) continue;
				for (const c of remaining) {
					if (filled.length >= 11) break;
					if (roleCounts[role] >= minimum) break;
					if (c.roleBucket !== role) continue;
					if (!canAddPlayer(c, filled, roleCounts, overseasCount)) continue;
					filled.push(c);
					roleCounts[role] += 1;
					if (c.isOverseas) overseasCount += 1;
				}
			}
			// Second pass: fill remaining spots
			for (const c of remaining) {
				if (filled.length >= 11) break;
				if (!canAddPlayer(c, filled, roleCounts, overseasCount)) continue;
				filled.push(c);
				roleCounts[c.roleBucket] += 1;
				if (c.isOverseas) overseasCount += 1;
			}

			return filled.sort((a, b) => b.contextScore - a.contextScore).slice(0, 11);
		}

		return manualPlayers.sort((a, b) => b.contextScore - a.contextScore);
	}

	// Auto-selection path
	const currentSeasonPool = candidates.filter(
		(p) => p.starts2026 > 0 || p.impactMatches2026 > 0 || p.matches2026 > 0
	);
	const selectionPool = currentSeasonPool.length >= 11 ? currentSeasonPool : candidates;

	const selected: XiPlayer[] = [];
	const roleCounts: Record<Role, number> = { Wicketkeeper: 0, Batter: 0, 'All-Rounder': 0, Bowler: 0 };
	let overseasCount = 0;

	for (const [role, minimum] of Object.entries(ROLE_MINIMUMS) as [Role, number][]) {
		const pool = selectionPool.filter((p) => p.roleBucket === role);
		for (const candidate of pool) {
			if (roleCounts[role] >= minimum) break;
			if (!canAddPlayer(candidate, selected, roleCounts, overseasCount)) continue;
			selected.push(candidate);
			roleCounts[role] += 1;
			if (candidate.isOverseas) overseasCount += 1;
		}
	}

	for (const candidate of selectionPool) {
		if (selected.length >= 11) break;
		if (!canAddPlayer(candidate, selected, roleCounts, overseasCount)) continue;
		selected.push(candidate);
		roleCounts[candidate.roleBucket] += 1;
		if (candidate.isOverseas) overseasCount += 1;
	}

	return selected.sort((a, b) => b.contextScore - a.contextScore).slice(0, 11);
}

/* ─── matchup context (uses smoothedEdge) ─── */

function applyMatchupContext(
	xi: XiPlayer[],
	opponentXi: XiPlayer[],
	pairLookup: Map<string, PairStat>
) {
	const topOpponentBowlers = [...opponentXi]
		.sort((a, b) => b.bowlingProjection - a.bowlingProjection)
		.slice(0, 5);
	const topOpponentBatters = [...opponentXi]
		.sort((a, b) => b.battingProjection - a.battingProjection)
		.slice(0, 6);

	return xi
		.map((player) => {
			const battingEdges = topOpponentBowlers
				.map((bowler) => {
					const pair = pairLookup.get(`${player.playerKey}|${bowler.playerKey}`);
					return pair?.smoothedEdge ?? pair?.edge ?? 0;
				})
				.filter((e) => e !== 0);
			const bowlingEdges = topOpponentBatters
				.map((batter) => {
					const pair = pairLookup.get(`${batter.playerKey}|${player.playerKey}`);
					return -(pair?.smoothedEdge ?? pair?.edge ?? 0);
				})
				.filter((e) => e !== 0);

			const battingBonus = clamp(average(battingEdges) * 0.16, -8, 8);
			const bowlingBonus = clamp(average(bowlingEdges) * 0.14, -7, 7);
			const matchupBonus =
				player.roleBucket === 'Bowler'
					? bowlingBonus * 1.1 + battingBonus * 0.2
					: player.roleBucket === 'All-Rounder'
						? battingBonus * 0.7 + bowlingBonus * 0.75
						: battingBonus * 1.05 + bowlingBonus * 0.18;

			return {
				...player,
				contextScore: round(player.contextScore + matchupBonus * 0.9),
				projectedPoints: round(player.projectedPoints + matchupBonus),
				battingProjection: round(player.battingProjection + battingBonus),
				bowlingProjection: round(player.bowlingProjection + bowlingBonus),
				floorPoints: round(player.floorPoints + matchupBonus * 0.58),
				ceilingPoints: round(player.ceilingPoints + Math.max(matchupBonus, 0) * 0.72),
				volatility: round(Math.max(0, player.volatility + Math.abs(matchupBonus) * 0.2))
			};
		})
		.sort((a, b) => b.contextScore - a.contextScore);
}

/* ─── team-level aggregates ─── */

function teamStrengths(xi: XiPlayer[]) {
	const battingCore = [...xi].sort((a, b) => b.battingProjection - a.battingProjection).slice(0, 6);
	const bowlingCore = [...xi].sort((a, b) => b.bowlingProjection - a.bowlingProjection).slice(0, 5);
	return {
		batting: average(battingCore.map((p) => p.battingProjection)),
		bowling: average(bowlingCore.map((p) => p.bowlingProjection))
	};
}

function teamPhaseStrengths(xi: XiPlayer[]) {
	// Death bowling strength (top 4 bowlers by bowling projection)
	const bowlers = [...xi].sort((a, b) => b.bowlingProjection - a.bowlingProjection).slice(0, 4);
	const deathBowling = average(bowlers.map((p) => p.bowling.deathIndex ?? p.bowling.index));

	// Powerplay batting (top 3 openers / top-order)
	const batters = [...xi].sort((a, b) => b.battingProjection - a.battingProjection).slice(0, 3);
	const ppBatting = average(batters.map((p) => p.batting.powerplayIndex ?? p.batting.index));

	return { deathBowling, ppBatting };
}

function teamFormScore(xi: XiPlayer[]) {
	return average(xi.map((p) => p.formScore ?? 0));
}

function teamStability(xi: XiPlayer[]) {
	return average(xi.map((p) => p.consistencyScore));
}

function teamFreshness(xi: XiPlayer[]) {
	return average(
		xi.map(
			(p) =>
				p.starts2026 * 1.2 +
				p.recentStarts2026 * 1.6 +
				p.impactMatches2026 * 0.35 +
				p.recencyScore * 8
		)
	);
}

function findPairEdges(pairLookup: Map<string, PairStat>, battingXi: XiPlayer[], bowlingXi: XiPlayer[]) {
	const batters = new Set(battingXi.slice(0, 5).map((p) => p.playerKey));
	const bowlers = new Set(
		[...bowlingXi].sort((a, b) => b.bowlingProjection - a.bowlingProjection).slice(0, 5).map((p) => p.playerKey)
	);

	return [...pairLookup.values()]
		.filter((item) => batters.has(item.batterKey) && bowlers.has(item.bowlerKey))
		.sort((a, b) => Math.abs(b.smoothedEdge ?? b.edge) - Math.abs(a.smoothedEdge ?? a.edge));
}

/* ─── projected team score (phase-aware) ─── */

function projectedTeamScore(
	venue: VenueProfile | undefined,
	battingStrength: number,
	opponentBowlingStrength: number,
	battingFirst: boolean,
	ppBatting: number,
	opponentDeathBowling: number
) {
	const venueBase = venue?.firstInningsAverage ?? 166;
	const scenarioBoost = battingFirst ? 5 : -2;

	// Core differential
	const coreDiff = (battingStrength - opponentBowlingStrength) * 0.26;

	// Phase adjustments: strong powerplay batting adds runs, strong death bowling removes them
	const ppAdjust = (ppBatting - 50) * 0.08; // normalized around 50 index
	const deathAdjust = -(opponentDeathBowling - 50) * 0.06;

	return round(venueBase + coreDiff + ppAdjust + deathAdjust + scenarioBoost);
}

/* ─── toss / batting-first derivation ─── */

function deriveBattingFirst(
	teamA: string,
	teamB: string,
	tossWinner?: string,
	tossDecision?: 'bat' | 'field',
	venueChaseWinRate?: number
) {
	if (tossWinner && tossDecision) {
		if (tossDecision === 'bat') return tossWinner;
		return tossWinner === teamA ? teamB : teamA;
	}
	// No toss: if venue favors chasing (>52%), assume winner would field → teamA bats
	// Otherwise default to teamA batting first
	if (venueChaseWinRate != null && venueChaseWinRate > 52) {
		return teamA; // assume toss winner would field, so "other team" bats first — default teamA
	}
	return teamA;
}

function sanitizeSelectedXi(team: TeamProfile, keys: string[] | undefined) {
	if (!keys?.length) return [];
	const teamPlayerKeys = new Set(team.squad.map((p) => p.playerKey));
	return [...new Set(keys)].filter((k) => teamPlayerKeys.has(k));
}

/* ─── impact player resolution ─── */

/**
 * Resolve an impact player from the team squad.
 * The impact player must NOT be in the already-selected XI.
 * Returns the scored XiPlayer or undefined.
 */
function resolveImpactPlayer(
	team: TeamProfile,
	xi: XiPlayer[],
	impactPlayerKey: string | undefined,
	venue: string,
	battingFirstTeam: string,
	priors: GlobalPriors | undefined
): XiPlayer | undefined {
	if (!impactPlayerKey) return undefined;
	const xiKeys = new Set(xi.map((p) => p.playerKey));
	// Impact player must be from squad but NOT in XI
	const player = team.squad.find((p) => p.playerKey === impactPlayerKey && !xiKeys.has(p.playerKey));
	if (!player) return undefined;
	return scoreCandidate(player, venue, battingFirstTeam, priors);
}

/**
 * Compute impact player quality score for a team.
 * Measures how much the 12th specialist player adds to the team's depth.
 */
function impactPlayerQuality(ip: XiPlayer | undefined): number {
	if (!ip) return 0;
	// Specialist bonus: impact players are typically picked for role depth
	const roleFactor = ip.role === 'All-Rounder' ? 1.2 : 1.0;
	// Quality = their projection quality weighted by IP experience
	const ipExperience = clamp(ip.impactMatches2026 / 3, 0.3, 1.2); // more IP appearances → more reliable
	return (ip.contextScore * 0.5 + ip.projectedPoints * 0.35 + ip.battingProjection * 0.08 + ip.bowlingProjection * 0.07) * roleFactor * ipExperience;
}

/* ─── Monte Carlo simulation ─── */

function monteCarloSimulation(
	projScoreA: number,
	projScoreB: number,
	volatilityA: number,
	volatilityB: number,
	baseWinProbA: number,
	iterations: number
): { winRateA: number; winRateB: number; distA: ScoreDistribution; distB: ScoreDistribution } {
	const rng = makeRng(137);
	let winsA = 0;
	let winsB = 0;
	const scoresA: number[] = [];
	const scoresB: number[] = [];

	// Std dev from volatility: scale team-level volatility to score std
	const stdA = Math.max(8, volatilityA * 0.6 + 12);
	const stdB = Math.max(8, volatilityB * 0.6 + 12);

	// Incorporate the base win probability as a slight bias
	const biasA = (baseWinProbA - 50) * 0.15;

	for (let i = 0; i < iterations; i++) {
		const sA = normalSample(rng, projScoreA + biasA, stdA);
		const sB = normalSample(rng, projScoreB - biasA, stdB);
		scoresA.push(sA);
		scoresB.push(sB);
		if (sA > sB) winsA++;
		else if (sB > sA) winsB++;
		else {
			// Tie: coin-flip (super-over proxy)
			if (rng() < 0.5) winsA++;
			else winsB++;
		}
	}

	scoresA.sort((a, b) => a - b);
	scoresB.sort((a, b) => a - b);

	const pctile = (arr: number[], p: number) => arr[Math.floor(arr.length * p)] ?? 0;

	return {
		winRateA: round((winsA / iterations) * 100),
		winRateB: round((winsB / iterations) * 100),
		distA: {
			low: round(pctile(scoresA, 0.1)),
			median: round(pctile(scoresA, 0.5)),
			high: round(pctile(scoresA, 0.9))
		},
		distB: {
			low: round(pctile(scoresB, 0.1)),
			median: round(pctile(scoresB, 0.5)),
			high: round(pctile(scoresB, 0.9))
		}
	};
}

/* ─── confidence score ─── */

function computeConfidence(
	modelWinProb: number,
	mcWinProb: number,
	sampleFactors: { h2hMatches: number; venueMatches: number; pairSamples: number }
): number {
	// Agreement between model and MC
	const agreement = 1 - Math.abs(modelWinProb - mcWinProb) / 50;

	// Decisiveness: how far from 50-50
	const decisiveness = Math.abs(modelWinProb - 50) / 43; // max ~43 from 50

	// Data quality: more H2H, venue, pair data → more confident
	const dataQuality = clamp(
		(Math.min(sampleFactors.h2hMatches, 20) / 20) * 0.3 +
		(Math.min(sampleFactors.venueMatches, 30) / 30) * 0.3 +
		(Math.min(sampleFactors.pairSamples, 100) / 100) * 0.4,
		0,
		1
	);

	return round(clamp((agreement * 0.4 + decisiveness * 0.35 + dataQuality * 0.25) * 100, 10, 95));
}

/* ─── main prediction ─── */

export function predictMatch(data: AppData, input: PredictionInput): PredictionResult {
	const teamA = findTeam(data, input.teamA);
	const teamB = findTeam(data, input.teamB);
	if (!teamA || !teamB) throw new Error('Unknown team selected.');
	if (teamA.name === teamB.name) throw new Error('Choose two different teams.');

	const venue = findVenue(data, input.venue);
	const weights: ModelWeights | undefined = data.modelWeights;
	const priors: GlobalPriors | undefined = data.globalPriors;

	const battingFirst =
		input.tossWinner && input.tossDecision
			? deriveBattingFirst(teamA.name, teamB.name, input.tossWinner, input.tossDecision)
			: input.battingFirst
				? input.battingFirst
				: deriveBattingFirst(teamA.name, teamB.name, undefined, undefined, venue?.chaseWinRate);

	const selectedXiA = sanitizeSelectedXi(teamA, input.selectedXiA);
	const selectedXiB = sanitizeSelectedXi(teamB, input.selectedXiB);

	const pairLookup = buildPairLookup(data.pairStats, input.venue);
	const baseTeamAXi = buildProbableXi(teamA, input.venue, battingFirst, selectedXiA, priors);
	const baseTeamBXi = buildProbableXi(teamB, input.venue, battingFirst, selectedXiB, priors);
	const teamAXi = applyMatchupContext(baseTeamAXi, baseTeamBXi, pairLookup);
	const teamBXi = applyMatchupContext(baseTeamBXi, baseTeamAXi, pairLookup);

	// Resolve impact players (12th man substitutes)
	const impactPlayerA = resolveImpactPlayer(teamA, teamAXi, input.impactPlayerA, input.venue, battingFirst, priors);
	const impactPlayerB = resolveImpactPlayer(teamB, teamBXi, input.impactPlayerB, input.venue, battingFirst, priors);

	// Build extended squads (XI + impact player) for strength calculations
	const teamAExtended = impactPlayerA ? [...teamAXi, impactPlayerA] : teamAXi;
	const teamBExtended = impactPlayerB ? [...teamBXi, impactPlayerB] : teamBXi;

	// Team strengths (use extended squad — 12 players means higher top-6/top-5 quality)
	const teamAUnits = teamStrengths(teamAExtended);
	const teamBUnits = teamStrengths(teamBExtended);
	const phaseA = teamPhaseStrengths(teamAExtended);
	const phaseB = teamPhaseStrengths(teamBExtended);

	const stabilityEdge = teamStability(teamAXi) - teamStability(teamBXi);
	const freshnessEdge = teamFreshness(teamAXi) - teamFreshness(teamBXi);
	const formEdge = teamFormScore(teamAXi) - teamFormScore(teamBXi);

	// Elo edge
	const eloA = teamA.eloRating ?? 1500;
	const eloB = teamB.eloRating ?? 1500;
	const eloEdge = (eloA - eloB) / 40; // ~25 pts diff = 0.625 edge unit

	// Venue edge
	const venueEdge =
		(teamVenueWinRate(teamA, input.venue) - teamVenueWinRate(teamB, input.venue)) * 0.14;

	// Head-to-head (use decayed win rates if available)
	const headToHead = getHeadToHeadRecord(data.headToHead, teamA.name, teamB.name);
	let headToHeadEdge = 0;
	if (headToHead) {
		const isAFirst = headToHead.teamA === teamA.name;
		const decayedA = isAFirst ? headToHead.decayedWinRateA : headToHead.decayedWinRateB;
		const decayedB = isAFirst ? headToHead.decayedWinRateB : headToHead.decayedWinRateA;
		// Use decayed if available, fall back to raw
		if (decayedA != null && decayedB != null) {
			headToHeadEdge = (decayedA - decayedB) * 40;
		} else {
			const rawA = isAFirst ? headToHead.winRateA : headToHead.winRateB;
			const rawB = isAFirst ? headToHead.winRateB : headToHead.winRateA;
			headToHeadEdge = (rawA - rawB) * 40;
		}
	}

	// Matchup edges (using smoothedEdge)
	const teamAMatchups = findPairEdges(pairLookup, teamAXi, teamBXi);
	const teamBMatchups = findPairEdges(pairLookup, teamBXi, teamAXi);
	const teamAMatchupEdge = teamAMatchups.slice(0, 4).reduce((s, i) => s + (i.smoothedEdge ?? i.edge), 0);
	const teamBMatchupEdge = teamBMatchups.slice(0, 4).reduce((s, i) => s + (i.smoothedEdge ?? i.edge), 0);

	// Toss edge with dew factor
	let tossEdge = 0;
	if (input.tossWinner && input.tossDecision) {
		const chasingBias = (venue?.chaseWinRate ?? 50) - 50;
		const dew = dewMultiplier(input.matchTime, venue?.chaseWinRate ?? 50);
		const adjustedChasingBias = chasingBias + dew * 6; // dew adds up to ~6 runs worth of advantage

		const tossFavored =
			input.tossDecision === 'field'
				? input.tossWinner
				: input.tossWinner === teamA.name
					? teamB.name
					: teamA.name;
		tossEdge = tossFavored === teamA.name ? adjustedChasingBias * 0.36 : -adjustedChasingBias * 0.36;
	}

	// Phase edges
	const deathBowlingEdge = phaseA.deathBowling - phaseB.deathBowling;
	const powerplayEdge = phaseA.ppBatting - phaseB.ppBatting;

	// Impact player edge
	const ipQualityA = impactPlayerQuality(impactPlayerA);
	const ipQualityB = impactPlayerQuality(impactPlayerB);
	const impactPlayerEdge = (ipQualityA - ipQualityB) * 0.25;

	const battingEdge = teamAUnits.batting - teamBUnits.batting;
	const bowlingEdge = teamAUnits.bowling - teamBUnits.bowling;
	const matchupEdge = teamAMatchupEdge - teamBMatchupEdge;

	// Use trained model weights if available, otherwise fall back to hand-tuned
	let totalEdge: number;
	if (weights) {
		totalEdge =
			(weights.intercept ?? 0) +
			battingEdge * (weights.battingEdge ?? 0.38) +
			bowlingEdge * (weights.bowlingEdge ?? 0.3) +
			venueEdge * (weights.venueEdge ?? 1.0) +
			eloEdge * (weights.eloEdge ?? 0.15) +
			formEdge * (weights.formEdge ?? 0.12) +
			tossEdge * (weights.tossEdge ?? 1.0) +
			headToHeadEdge * (weights.h2hEdge ?? 0.2) +
			stabilityEdge * (weights.stabilityEdge ?? 0.08) +
			freshnessEdge * (weights.freshnessEdge ?? 0.18) +
			matchupEdge * (weights.matchupEdge ?? 0.22) +
			deathBowlingEdge * (weights.deathBowlingEdge ?? 0.1) +
			powerplayEdge * (weights.powerplayEdge ?? 0.08) +
			impactPlayerEdge; // hand-tuned, not in trained model
	} else {
		totalEdge =
			battingEdge * 0.38 +
			bowlingEdge * 0.3 +
			venueEdge +
			eloEdge * 0.15 +
			formEdge * 0.12 +
			stabilityEdge * 0.08 +
			freshnessEdge * 0.18 +
			matchupEdge * 0.22 +
			tossEdge +
			headToHeadEdge * 0.2 +
			deathBowlingEdge * 0.1 +
			powerplayEdge * 0.08 +
			impactPlayerEdge;
	}

	const winProbabilityA = round(clamp(logistic(totalEdge / 10) * 100, 7, 93));
	const winProbabilityB = round(100 - winProbabilityA);

	// Phase-aware projected scores
	const projectedScoreA = projectedTeamScore(
		venue, teamAUnits.batting, teamBUnits.bowling,
		battingFirst === teamA.name,
		phaseA.ppBatting, phaseB.deathBowling
	);
	const projectedScoreB = projectedTeamScore(
		venue, teamBUnits.batting, teamAUnits.bowling,
		battingFirst === teamB.name,
		phaseB.ppBatting, phaseA.deathBowling
	);

	// Monte Carlo simulation
	const teamAVolatility = average(teamAXi.map((p) => p.volatility));
	const teamBVolatility = average(teamBXi.map((p) => p.volatility));
	const mc = monteCarloSimulation(
		projectedScoreA, projectedScoreB,
		teamAVolatility, teamBVolatility,
		winProbabilityA,
		MONTE_CARLO_ITERATIONS
	);

	// Confidence
	const pairSamples = teamAMatchups.length + teamBMatchups.length;
	const confidence = computeConfidence(winProbabilityA, mc.winRateA, {
		h2hMatches: headToHead?.matches ?? 0,
		venueMatches: venue?.matches ?? 0,
		pairSamples
	});

	// Factors for display
	const factors: MatchFactor[] = [
		{
			label: 'Elo Rating',
			detail: `${teamA.shortName} ${round(eloA)} vs ${teamB.shortName} ${round(eloB)}`,
			edge: round(Math.abs(eloEdge)),
			favoredTeam: eloEdge >= 0 ? teamA.name : teamB.name
		},
		{
			label: 'Form (EWMA)',
			detail: `${teamA.shortName} ${round(teamFormScore(teamAXi))} vs ${teamB.shortName} ${round(teamFormScore(teamBXi))}`,
			edge: round(Math.abs(formEdge)),
			favoredTeam: formEdge >= 0 ? teamA.name : teamB.name
		},
		{
			label: 'Probable XI Stability',
			detail: `${teamA.shortName} ${round(teamStability(teamAXi))} vs ${teamB.shortName} ${round(teamStability(teamBXi))}`,
			edge: round(Math.abs(stabilityEdge)),
			favoredTeam: stabilityEdge >= 0 ? teamA.name : teamB.name
		},
		{
			label: '2026 XI Continuity',
			detail: `${teamA.shortName} ${round(teamFreshness(teamAXi))} vs ${teamB.shortName} ${round(teamFreshness(teamBXi))} from current 2026 usage`,
			edge: round(Math.abs(freshnessEdge)),
			favoredTeam: freshnessEdge >= 0 ? teamA.name : teamB.name
		},
		{
			label: 'Batting Core',
			detail: `${teamA.shortName} ${round(teamAUnits.batting)} vs ${teamB.shortName} ${round(teamBUnits.batting)}`,
			edge: round(Math.abs(battingEdge)),
			favoredTeam: battingEdge >= 0 ? teamA.name : teamB.name
		},
		{
			label: 'Bowling Pressure',
			detail: `${teamA.shortName} ${round(teamAUnits.bowling)} vs ${teamB.shortName} ${round(teamBUnits.bowling)}`,
			edge: round(Math.abs(bowlingEdge)),
			favoredTeam: bowlingEdge >= 0 ? teamA.name : teamB.name
		},
		{
			label: 'Death Bowling',
			detail: `${teamA.shortName} ${round(phaseA.deathBowling)} vs ${teamB.shortName} ${round(phaseB.deathBowling)} death-over index`,
			edge: round(Math.abs(deathBowlingEdge)),
			favoredTeam: deathBowlingEdge >= 0 ? teamA.name : teamB.name
		},
		{
			label: 'Powerplay Batting',
			detail: `${teamA.shortName} ${round(phaseA.ppBatting)} vs ${teamB.shortName} ${round(phaseB.ppBatting)} PP index`,
			edge: round(Math.abs(powerplayEdge)),
			favoredTeam: powerplayEdge >= 0 ? teamA.name : teamB.name
		},
		{
			label: 'Venue Comfort',
			detail: `${teamA.shortName} ${round(teamVenueWinRate(teamA, input.venue))}% vs ${teamB.shortName} ${round(teamVenueWinRate(teamB, input.venue))}% venue win rate`,
			edge: round(Math.abs(venueEdge)),
			favoredTeam: venueEdge >= 0 ? teamA.name : teamB.name
		},
		{
			label: 'Batter vs Bowler Matchups',
			detail: `${teamA.shortName} edge ${round(teamAMatchupEdge)} vs ${teamB.shortName} edge ${round(teamBMatchupEdge)} at ${input.venue}`,
			edge: round(Math.abs(matchupEdge)),
			favoredTeam: matchupEdge >= 0 ? teamA.name : teamB.name
		},
		{
			label: 'Head-to-Head (Decayed)',
			detail: headToHead
				? `${headToHead.matches} matches, weighted by recency`
				: 'No H2H data',
			edge: round(Math.abs(headToHeadEdge)),
			favoredTeam: headToHeadEdge >= 0 ? teamA.name : teamB.name
		}
	];

	// Add impact player factor only if at least one team has an impact player
	if (impactPlayerA || impactPlayerB) {
		const ipDetailA = impactPlayerA ? `${impactPlayerA.name} (${impactPlayerA.role})` : 'None';
		const ipDetailB = impactPlayerB ? `${impactPlayerB.name} (${impactPlayerB.role})` : 'None';
		factors.push({
			label: 'Impact Player',
			detail: `${teamA.shortName}: ${ipDetailA} vs ${teamB.shortName}: ${ipDetailB}`,
			edge: round(Math.abs(impactPlayerEdge)),
			favoredTeam: impactPlayerEdge >= 0 ? teamA.name : teamB.name
		});
	}

	factors.sort((a, b) => b.edge - a.edge);

	return {
		teamA: teamA.name,
		teamB: teamB.name,
		venue: input.venue,
		battingFirst,
		tossWinner: input.tossWinner,
		tossDecision: input.tossDecision,
		matchTime: input.matchTime,
		winProbabilityA,
		winProbabilityB,
		monteCarloWinRateA: mc.winRateA,
		monteCarloWinRateB: mc.winRateB,
		projectedScoreA,
		projectedScoreB,
		scoreDistA: mc.distA,
		scoreDistB: mc.distB,
		eloA: round(eloA),
		eloB: round(eloB),
		confidence,
		teamAXi,
		teamBXi,
		impactPlayerA,
		impactPlayerB,
		factors,
		matchups: [...teamAMatchups.slice(0, 3), ...teamBMatchups.slice(0, 3)].slice(0, 6),
		venueProfile: venue
	};
}

/* ─── fantasy lineup (with pruning) ─── */

function isValidLineup(lineup: XiPlayer[]) {
	const counts: Record<Role, number> = { Wicketkeeper: 0, Batter: 0, 'All-Rounder': 0, Bowler: 0 };
	const teamCounts = new Map<string, number>();
	let credits = 0;

	for (const player of lineup) {
		counts[player.roleBucket] += 1;
		credits += player.fantasyCredit;
		teamCounts.set(player.currentTeam, (teamCounts.get(player.currentTeam) ?? 0) + 1);
	}

	return (
		counts.Wicketkeeper >= 1 &&
		counts.Wicketkeeper <= 4 &&
		counts.Batter >= 3 &&
		counts.Batter <= 6 &&
		counts['All-Rounder'] >= 1 &&
		counts['All-Rounder'] <= 4 &&
		counts.Bowler >= 3 &&
		counts.Bowler <= 6 &&
		Math.max(...teamCounts.values()) <= 7 &&
		credits <= 100
	);
}

function lineupScore(
	lineup: XiPlayer[],
	favoredTeam: string,
	strategy: ReturnType<typeof strategyOf>
) {
	const expected = lineup.reduce((s, p) => s + p.projectedPoints, 0);
	const floor = lineup.reduce((s, p) => s + p.floorPoints, 0);
	const ceiling = lineup.reduce((s, p) => s + p.ceilingPoints, 0);
	const volatility = lineup.reduce((s, p) => s + p.volatility, 0);
	const favoriteCount = lineup.filter((p) => p.currentTeam === favoredTeam).length;
	const lowAvailabilityCount = lineup.filter((p) => p.availability < 0.72).length;

	if (strategy === 'grand') {
		return expected * 0.58 + ceiling * 0.34 - floor * 0.08 - volatility * 0.06 + favoriteCount * 1.3;
	}
	if (strategy === 'balanced') {
		return expected * 0.56 + floor * 0.28 + ceiling * 0.12 - volatility * 0.12 + favoriteCount * 0.8;
	}

	return (
		expected * 0.52 +
		floor * 0.4 +
		ceiling * 0.06 -
		volatility * 0.2 +
		favoriteCount * 1.1 -
		lowAvailabilityCount * 7
	);
}

/**
 * Brute-force C(n,11) with early pruning:
 * - Track running role counts and overseas count
 * - Prune branches that can't possibly satisfy role minimums with remaining slots
 * - Prune if overseas count already exceeded
 * - Prune if any role exceeds its maximum
 * - Prune if credits will exceed 100
 */
function chooseBestLineup(pool: XiPlayer[], favoredTeam: string, strategy: ReturnType<typeof strategyOf>) {
	let bestLineup: XiPlayer[] = [];
	let bestScore = -Infinity;

	// Pre-compute suffix role availability for pruning
	const n = pool.length;
	const suffixRoles: Record<Role, number>[] = new Array(n + 1);
	suffixRoles[n] = { Wicketkeeper: 0, Batter: 0, 'All-Rounder': 0, Bowler: 0 };
	for (let i = n - 1; i >= 0; i--) {
		suffixRoles[i] = { ...suffixRoles[i + 1] };
		suffixRoles[i][pool[i].roleBucket] += 1;
	}

	function walk(
		start: number,
		lineup: XiPlayer[],
		roleCounts: Record<Role, number>,
		overseasCount: number,
		credits: number,
		teamCounts: Map<string, number>
	) {
		if (lineup.length === 11) {
			// Final role validation (should always pass with pruning, but safety check)
			for (const [role, min] of Object.entries(ROLE_MINIMUMS) as [Role, number][]) {
				if (roleCounts[role] < min) return;
			}
			const score = lineupScore(lineup, favoredTeam, strategy);
			if (score > bestScore) {
				bestScore = score;
				bestLineup = [...lineup];
			}
			return;
		}

		const remaining = 11 - lineup.length;

		for (let i = start; i <= n - remaining; i++) {
			const player = pool[i];

			// Prune: role maximum exceeded
			if (roleCounts[player.roleBucket] >= ROLE_MAXIMUMS[player.roleBucket]) continue;

			// Prune: overseas limit
			if (player.isOverseas && overseasCount >= MAX_OVERSEAS) continue;

			// Prune: team limit (max 7 from one team)
			const currentTeamCount = teamCounts.get(player.currentTeam) ?? 0;
			if (currentTeamCount >= 7) continue;

			// Prune: credit limit
			if (credits + player.fantasyCredit > 100) continue;

			// Prune: can we still fill role minimums with remaining pool?
			const newRoleCounts = { ...roleCounts };
			newRoleCounts[player.roleBucket] += 1;
			const slotsAfter = remaining - 1; // slots left after adding this player
			const suffixAfter = suffixRoles[i + 1]; // roles available in pool[i+1..n-1]

			let canFillMinimums = true;
			let slotsNeeded = 0;
			for (const [role, min] of Object.entries(ROLE_MINIMUMS) as [Role, number][]) {
				const deficit = Math.max(0, min - newRoleCounts[role]);
				if (deficit > 0 && deficit > suffixAfter[role]) {
					canFillMinimums = false;
					break;
				}
				slotsNeeded += deficit;
			}
			if (!canFillMinimums || slotsNeeded > slotsAfter) continue;

			// Add player and recurse
			lineup.push(player);
			const newOverseas = overseasCount + (player.isOverseas ? 1 : 0);
			teamCounts.set(player.currentTeam, currentTeamCount + 1);

			walk(i + 1, lineup, newRoleCounts, newOverseas, credits + player.fantasyCredit, teamCounts);

			lineup.pop();
			teamCounts.set(player.currentTeam, currentTeamCount);
		}
	}

	const initRoles: Record<Role, number> = { Wicketkeeper: 0, Batter: 0, 'All-Rounder': 0, Bowler: 0 };
	walk(0, [], initRoles, 0, 0, new Map());
	return bestLineup;
}

/**
 * Greedy fallback when brute-force chooseBestLineup returns empty.
 * Strategy:
 * 1. Fill role minimums first, picking highest-scoring eligible player per required role slot
 * 2. Fill remaining 3 flex slots with best available players satisfying constraints
 * 3. If no valid lineup at creditLimit=100, retry with progressively relaxed credit caps
 */
function greedyFallbackLineup(
	pool: XiPlayer[],
	favoredTeam: string,
	strategy: ReturnType<typeof strategyOf>
): XiPlayer[] {
	// Try with increasing credit limits
	for (let creditLimit = 100; creditLimit <= 130; creditLimit += 5) {
		const result = greedyAttempt(pool, favoredTeam, strategy, creditLimit);
		if (result.length === 11) return result;
	}
	return [];
}

function greedyAttempt(
	pool: XiPlayer[],
	favoredTeam: string,
	strategy: ReturnType<typeof strategyOf>,
	creditLimit: number
): XiPlayer[] {
	const lineup: XiPlayer[] = [];
	const used = new Set<string>();
	const roleCounts: Record<Role, number> = { Wicketkeeper: 0, Batter: 0, 'All-Rounder': 0, Bowler: 0 };
	const teamCounts = new Map<string, number>();
	let overseasCount = 0;
	let credits = 0;

	function canAdd(player: XiPlayer): boolean {
		if (used.has(player.playerKey)) return false;
		if (roleCounts[player.roleBucket] >= ROLE_MAXIMUMS[player.roleBucket]) return false;
		if (player.isOverseas && overseasCount >= MAX_OVERSEAS) return false;
		if ((teamCounts.get(player.currentTeam) ?? 0) >= 7) return false;
		if (credits + player.fantasyCredit > creditLimit) return false;
		return true;
	}

	function addPlayer(player: XiPlayer) {
		lineup.push(player);
		used.add(player.playerKey);
		roleCounts[player.roleBucket] += 1;
		credits += player.fantasyCredit;
		if (player.isOverseas) overseasCount += 1;
		teamCounts.set(player.currentTeam, (teamCounts.get(player.currentTeam) ?? 0) + 1);
	}

	// Sort pool by projected points descending for greedy selection
	const sorted = [...pool].sort((a, b) => b.projectedPoints - a.projectedPoints);

	// Phase 1: Fill role minimums. For each role that needs filling, pick best available.
	const roleOrder: Role[] = ['Wicketkeeper', 'All-Rounder', 'Bowler', 'Batter'];
	for (const role of roleOrder) {
		const min = ROLE_MINIMUMS[role];
		while (roleCounts[role] < min) {
			const candidate = sorted.find((p) => p.roleBucket === role && canAdd(p));
			if (!candidate) break; // Can't fill this minimum — will fail later
			addPlayer(candidate);
		}
	}

	// Phase 2: Fill remaining slots (up to 11) with best available regardless of role
	while (lineup.length < 11) {
		const candidate = sorted.find((p) => canAdd(p));
		if (!candidate) break;
		addPlayer(candidate);
	}

	// Validate the result has proper role minimums
	if (lineup.length === 11) {
		for (const [role, min] of Object.entries(ROLE_MINIMUMS) as [Role, number][]) {
			if (roleCounts[role] < min) return []; // Failed — role minimum not met
		}
	}

	return lineup;
}

export function buildFantasyLineup(data: AppData, input: PredictionInput): FantasyResult {
	const prediction = predictMatch(data, input);
	const strategy = strategyOf(input);
	const favoredTeam =
		prediction.winProbabilityA >= prediction.winProbabilityB ? prediction.teamA : prediction.teamB;

	const candidatePool = [...prediction.teamAXi, ...prediction.teamBXi]
		.map((player) => {
			// Weight fantasy projections by expected balls faced / overs bowled
			const ballsWeight = clamp((player.expectedBallsFaced ?? 20) / 25, 0.5, 1.3);
			const oversWeight = clamp((player.expectedOversBowled ?? 2) / 3.5, 0.4, 1.4);

			const roleMultiplier =
				player.roleBucket === 'Bowler'
					? oversWeight
					: player.roleBucket === 'All-Rounder'
						? (ballsWeight * 0.5 + oversWeight * 0.5)
						: ballsWeight;

			return {
				...player,
				projectedPoints: round(
					(player.projectedPoints +
						(player.roleBucket === 'All-Rounder' ? 8 : 0) +
						(player.roleBucket === 'Wicketkeeper' ? 3 : 0) +
						(player.currentTeam === favoredTeam ? 2 : 0) +
						(strategy === 'safe' ? player.floorPoints * 0.1 : 0) +
						(strategy === 'grand' ? player.ceilingPoints * 0.08 : 0)) *
					roleMultiplier
				),
				floorPoints: round(
					player.floorPoints + (strategy === 'safe' ? player.consistencyScore * 0.08 : 0)
				),
				ceilingPoints: round(
					player.ceilingPoints + (strategy === 'grand' ? player.ceilingPoints * 0.06 : 0)
				),
				volatility: round(player.volatility * (strategy === 'safe' ? 1.12 : 0.96))
			};
		})
		.filter((p) => strategy !== 'safe' || p.availability >= 0.58)
		.sort((a, b) =>
			strategy === 'safe'
				? b.consistencyScore - a.consistencyScore || b.projectedPoints - a.projectedPoints
				: b.projectedPoints - a.projectedPoints
		);

	let lineup = chooseBestLineup(candidatePool, favoredTeam, strategy);
	let usedGreedyFallback = false;

	if (!lineup.length) {
		// Brute-force failed (likely credit constraint too tight) — try greedy fallback
		lineup = greedyFallbackLineup(candidatePool, favoredTeam, strategy);
		usedGreedyFallback = true;
	}

	if (!lineup.length) {
		throw new Error('Unable to build a legal fantasy lineup from the current candidate pool.');
	}

	const captain = [...lineup]
		.sort(
			(a, b) =>
				(strategy === 'safe'
					? b.floorPoints * 0.58 + b.projectedPoints * 0.42 + (b.roleBucket === 'All-Rounder' ? 6 : 0)
					: b.projectedPoints + (b.roleBucket === 'All-Rounder' ? 8 : 0) + b.ceilingPoints * 0.12) -
				(strategy === 'safe'
					? a.floorPoints * 0.58 + a.projectedPoints * 0.42 + (a.roleBucket === 'All-Rounder' ? 6 : 0)
					: a.projectedPoints + (a.roleBucket === 'All-Rounder' ? 8 : 0) + a.ceilingPoints * 0.12)
		)[0];

	const viceCaptain = [...lineup]
		.filter((p) => p.playerKey !== captain.playerKey)
		.sort((a, b) =>
			strategy === 'safe'
				? b.floorPoints - a.floorPoints || b.projectedPoints - a.projectedPoints
				: b.projectedPoints - a.projectedPoints || b.ceilingPoints - a.ceilingPoints
		)[0];

	return {
		teamA: prediction.teamA,
		teamB: prediction.teamB,
		venue: prediction.venue,
		strategy,
		lineup: lineup.sort((a, b) => b.projectedPoints - a.projectedPoints),
		captain,
		viceCaptain,
		totalProjectedPoints: round(lineup.reduce((s, p) => s + p.projectedPoints, 0)),
		totalCredits: round(lineup.reduce((s, p) => s + p.fantasyCredit, 0)),
		notes: [
			'Credits are local proxy credits built from player quality and role volume, not official Dream11 credits.',
			'Safe mode leans toward floor, consistency, availability, and secure role volume.',
			'Projections are weighted by expected balls faced / overs bowled from batting position data.',
			'Captain and vice-captain are chosen for repeatable output first, not just raw ceiling.',
			`Model uses trained logistic regression weights${data.modelWeights ? ' (active)' : ' (fallback)'} with ${MONTE_CARLO_ITERATIONS.toLocaleString()} Monte Carlo simulations.`,
			...(usedGreedyFallback
				? ['Lineup was built using greedy fallback (optimal brute-force search could not satisfy all constraints simultaneously).']
				: [])
		]
	};
}

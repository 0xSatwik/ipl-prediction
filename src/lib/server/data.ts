// @ts-ignore — Vite handles JSON imports at build time; no node:fs needed for Cloudflare Workers
import appDataJson from '../../../data/generated/app-data.json';

import type { AppData, TeamProfile, VenueProfile } from '$lib/types';

/**
 * App data is imported statically by Vite at build time.
 * This means the JSON is bundled into the server output and works on
 * Cloudflare Workers (which have no node:fs access).
 *
 * The in-memory reference avoids re-parsing on every request.
 */
const cachedData: AppData = appDataJson as unknown as AppData;

export async function getAppData(): Promise<AppData> {
	return cachedData;
}

export async function getHomePayload() {
	const data = await getAppData();
	// Prefer the first upcoming match whose venue exists in our data.
	// Fall back to any fixture with a known venue, then to the first fixture overall.
	const featuredFixture =
		data.fixtures.find((f) => f.status !== 'Post' && findVenue(data, f.venue)) ??
		data.fixtures.find((f) => findVenue(data, f.venue)) ??
		data.fixtures[0];
	const defaultContext = featuredFixture
		? {
				teamA: featuredFixture.teamA,
				teamB: featuredFixture.teamB,
				venue: featuredFixture.venue,
				tossWinner: '',
				tossDecision: '' as '' | 'bat' | 'field',
				battingFirst: ''
			}
		: {
				teamA: 'Royal Challengers Bengaluru',
				teamB: 'Mumbai Indians',
				venue: 'M Chinnaswamy Stadium',
				tossWinner: '',
				tossDecision: '' as '' | 'bat' | 'field',
				battingFirst: ''
			};

	return {
		builtAt: data.builtAt,
		source: data.source,
		dashboard: data.dashboard,
		fixtures: data.fixtures.slice(0, 6),
		teams: data.teams.map((team) => ({
			id: team.id,
			name: team.name,
			shortName: team.shortName,
			slug: team.slug,
			colors: team.colors,
			battingRating: team.battingRating,
			bowlingRating: team.bowlingRating,
			winRate: team.winRate,
			eloRating: team.eloRating,
			topPlayers: team.squad.slice(0, 6)
		})),
		players: data.players,
		venues: data.venues,
		defaultContext,
		hasModelWeights: !!data.modelWeights,
		hasGlobalPriors: !!data.globalPriors
	};
}

export function findTeam(data: AppData, teamName: string): TeamProfile | undefined {
	return data.teams.find((team) => team.name === teamName);
}

export function findVenue(data: AppData, venueName: string): VenueProfile | undefined {
	return data.venues.find((venue) => venue.name === venueName);
}

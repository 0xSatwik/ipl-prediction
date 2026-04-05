import { json } from '@sveltejs/kit';

import { buildFantasyLineup } from '$lib/server/engine';
import { getAppData } from '$lib/server/data';
import type { PredictionInput } from '$lib/types';

function validatePayload(payload: unknown): { valid: true; data: PredictionInput } | { valid: false; message: string } {
	if (!payload || typeof payload !== 'object') {
		return { valid: false, message: 'Request body must be a JSON object.' };
	}

	const p = payload as Record<string, unknown>;

	if (typeof p.teamA !== 'string' || !p.teamA.trim()) {
		return { valid: false, message: 'teamA is required and must be a non-empty string.' };
	}
	if (typeof p.teamB !== 'string' || !p.teamB.trim()) {
		return { valid: false, message: 'teamB is required and must be a non-empty string.' };
	}
	if (typeof p.venue !== 'string' || !p.venue.trim()) {
		return { valid: false, message: 'venue is required and must be a non-empty string.' };
	}
	if (p.teamA === p.teamB) {
		return { valid: false, message: 'teamA and teamB must be different.' };
	}

	if (p.tossWinner !== undefined && typeof p.tossWinner !== 'string') {
		return { valid: false, message: 'tossWinner must be a string.' };
	}
	if (p.tossDecision !== undefined && p.tossDecision !== 'bat' && p.tossDecision !== 'field') {
		return { valid: false, message: 'tossDecision must be "bat" or "field".' };
	}
	if (p.battingFirst !== undefined && typeof p.battingFirst !== 'string') {
		return { valid: false, message: 'battingFirst must be a string.' };
	}
	if (p.strategy !== undefined && !['safe', 'balanced', 'grand'].includes(p.strategy as string)) {
		return { valid: false, message: 'strategy must be "safe", "balanced", or "grand".' };
	}
	if (p.matchTime !== undefined && p.matchTime !== 'afternoon' && p.matchTime !== 'evening') {
		return { valid: false, message: 'matchTime must be "afternoon" or "evening".' };
	}
	if (p.selectedXiA !== undefined && (!Array.isArray(p.selectedXiA) || p.selectedXiA.some((k: unknown) => typeof k !== 'string'))) {
		return { valid: false, message: 'selectedXiA must be an array of strings.' };
	}
	if (p.selectedXiB !== undefined && (!Array.isArray(p.selectedXiB) || p.selectedXiB.some((k: unknown) => typeof k !== 'string'))) {
		return { valid: false, message: 'selectedXiB must be an array of strings.' };
	}
	if (p.impactPlayerA !== undefined && typeof p.impactPlayerA !== 'string') {
		return { valid: false, message: 'impactPlayerA must be a string (player key).' };
	}
	if (p.impactPlayerB !== undefined && typeof p.impactPlayerB !== 'string') {
		return { valid: false, message: 'impactPlayerB must be a string (player key).' };
	}

	return { valid: true, data: payload as PredictionInput };
}

export async function POST({ request }) {
	try {
		const raw = await request.json();
		const result = validatePayload(raw);

		if (!result.valid) {
			return json({ message: result.message }, { status: 400 });
		}

		const data = await getAppData();
		const lineup = buildFantasyLineup(data, result.data);
		return json(lineup);
	} catch (error) {
		const message = error instanceof Error ? error.message : 'Fantasy lineup generation failed.';
		return json({ message }, { status: 400 });
	}
}

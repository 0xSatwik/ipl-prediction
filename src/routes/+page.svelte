<svelte:options runes={false} />

<script lang="ts">
	import { onMount } from 'svelte';
	import type { FantasyResult, Fixture, PlayerProfile, PredictionResult } from '$lib/types';

	export let data: {
		builtAt: string;
		source: { historical: string; currentSquads: string; notes: string[] };
		dashboard: {
			matches: number;
			season2026Matches: number;
			seasons: number;
			venues: number;
			currentPlayers: number;
			historyWindow: string;
		};
		fixtures: Fixture[];
		teams: Array<{
			id: string;
			name: string;
			shortName: string;
			slug: string;
			colors: string[];
			battingRating: number;
			bowlingRating: number;
			winRate: number;
			eloRating: number;
			topPlayers: PlayerProfile[];
		}>;
		players: PlayerProfile[];
		venues: Array<{
			name: string;
			slug: string;
			city: string;
			matches: number;
			firstInningsAverage: number;
			chaseWinRate: number;
			topBatters: Array<{ name: string; team: string; battingScore: number }>;
			topBowlers: Array<{ name: string; team: string; bowlingScore: number }>;
		}>;
		defaultContext: {
			teamA: string;
			teamB: string;
			venue: string;
			tossWinner: string;
			tossDecision: '' | 'bat' | 'field';
			battingFirst: string;
		};
		hasModelWeights: boolean;
		hasGlobalPriors: boolean;
	};

	let form = {
		...data.defaultContext,
		strategy: 'safe' as 'safe' | 'balanced' | 'grand',
		matchTime: 'evening' as 'afternoon' | 'evening',
		impactPlayerA: '',
		impactPlayerB: ''
	};
	let prediction: PredictionResult | null = null;
	let fantasy: FantasyResult | null = null;
	let loadingPrediction = false;
	let loadingFantasy = false;
	let error = '';
	let fantasyError = '';
	let useCustomXi = false;
	let selectedXiA: string[] = [];
	let selectedXiB: string[] = [];
	let selectionError = '';
	let activeTab: 'predict' | 'fantasy' = 'predict';

	$: venueDetails = data.venues.find((v) => v.name === form.venue) ?? data.venues[0];
	$: builtLabel = new Date(data.builtAt).toLocaleString();
	$: teamAPlayers = data.players
		.filter((p) => p.currentTeam === form.teamA)
		.sort((a, b) => b.starts2026 - a.starts2026 || b.selectionScore - a.selectionScore || a.name.localeCompare(b.name));
	$: teamBPlayers = data.players
		.filter((p) => p.currentTeam === form.teamB)
		.sort((a, b) => b.starts2026 - a.starts2026 || b.selectionScore - a.selectionScore || a.name.localeCompare(b.name));
	$: {
		const next = selectedXiA.filter((k) => teamAPlayers.some((p) => p.playerKey === k));
		if (next.length !== selectedXiA.length) selectedXiA = next;
	}
	$: {
		const next = selectedXiB.filter((k) => teamBPlayers.some((p) => p.playerKey === k));
		if (next.length !== selectedXiB.length) selectedXiB = next;
	}
	$: if (form.tossWinner && form.tossWinner !== form.teamA && form.tossWinner !== form.teamB) {
		form = { ...form, tossWinner: '' };
	}
	$: {
		if (form.tossWinner && form.tossDecision) {
			const auto = form.tossDecision === 'bat' ? form.tossWinner : form.tossWinner === form.teamA ? form.teamB : form.teamA;
			if (form.battingFirst !== auto) form = { ...form, battingFirst: auto };
		} else if (!form.tossWinner) {
			// No toss selected — clear battingFirst so engine derives it
			if (form.battingFirst !== '') form = { ...form, battingFirst: '', tossDecision: '' as '' | 'bat' | 'field' };
		}
	}

	// Computed impact player options: squad members NOT in manually selected XI
	$: impactCandidatesA = teamAPlayers.filter((p) =>
		!selectedXiA.includes(p.playerKey) && (p.starts2026 > 0 || p.impactMatches2026 > 0 || p.matches2026 > 0)
	);
	$: impactCandidatesB = teamBPlayers.filter((p) =>
		!selectedXiB.includes(p.playerKey) && (p.starts2026 > 0 || p.impactMatches2026 > 0 || p.matches2026 > 0)
	);

	$: teamASN = data.teams.find((t) => t.name === form.teamA)?.shortName ?? form.teamA;
	$: teamBSN = data.teams.find((t) => t.name === form.teamB)?.shortName ?? form.teamB;

	function pct(v: number) { return `${v.toFixed(1)}%`; }

	function buildPayload() {
		return {
			teamA: form.teamA,
			teamB: form.teamB,
			venue: form.venue,
			tossWinner: form.tossWinner || undefined,
			tossDecision: form.tossDecision || undefined,
			battingFirst: form.battingFirst || undefined,
			matchTime: form.matchTime,
			strategy: form.strategy,
			selectedXiA: useCustomXi && selectedXiA.length ? selectedXiA : undefined,
			selectedXiB: useCustomXi && selectedXiB.length ? selectedXiB : undefined,
			impactPlayerA: form.impactPlayerA || undefined,
			impactPlayerB: form.impactPlayerB || undefined
		};
	}

	function toggleXi(side: 'A' | 'B', key: string) {
		selectionError = '';
		const cur = side === 'A' ? selectedXiA : selectedXiB;
		if (cur.includes(key)) {
			const next = cur.filter((k) => k !== key);
			if (side === 'A') selectedXiA = next; else selectedXiB = next;
			return;
		}
		if (cur.length >= 11) { selectionError = `Max 11 players for Team ${side}.`; return; }
		if (side === 'A') selectedXiA = [...selectedXiA, key]; else selectedXiB = [...selectedXiB, key];
	}

	function clearXi(side: 'A' | 'B') {
		selectionError = '';
		if (side === 'A') selectedXiA = []; else selectedXiB = [];
	}

	function loadFixture(fixture: Fixture) {
		form = {
			teamA: fixture.teamA, teamB: fixture.teamB, venue: fixture.venue,
			tossWinner: '', tossDecision: '' as '' | 'bat' | 'field', battingFirst: '',
			strategy: 'safe', matchTime: 'evening',
			impactPlayerA: '', impactPlayerB: ''
		};
		useCustomXi = false; selectedXiA = []; selectedXiB = []; selectionError = '';
		runAll();
	}

	async function runPrediction() {
		loadingPrediction = true; error = '';
		const res = await fetch('/api/predict', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(buildPayload()) });
		const body = await res.json();
		if (!res.ok) { error = body.message ?? 'Prediction failed.'; prediction = null; } else { prediction = body; }
		loadingPrediction = false;
	}

	async function runFantasy() {
		loadingFantasy = true; fantasyError = '';
		const res = await fetch('/api/dream11', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(buildPayload()) });
		const body = await res.json();
		if (!res.ok) { fantasyError = body.message ?? 'Fantasy failed.'; fantasy = null; } else { fantasy = body; }
		loadingFantasy = false;
	}

	function runAll() { runPrediction(); runFantasy(); }

	function roleIcon(role: string) {
		if (role === 'Wicketkeeper') return 'WK';
		if (role === 'Batter') return 'BAT';
		if (role === 'All-Rounder') return 'AR';
		return 'BWL';
	}

	function roleColor(role: string) {
		if (role === 'Wicketkeeper') return '#f59e0b';
		if (role === 'Batter') return '#3b82f6';
		if (role === 'All-Rounder') return '#8b5cf6';
		return '#10b981';
	}

	onMount(runAll);
</script>

<svelte:head>
	<title>IPL Match Predictor 2026</title>
	<meta name="description" content="ML-powered IPL match prediction with Monte Carlo simulation, Elo ratings, and fantasy XI builder." />
</svelte:head>

<div class="page">
	<!-- Hero Section -->
	<section class="hero">
		<div class="hero-content">
			<div class="hero-badge">IPL 2026</div>
			<h1>Match Prediction Engine</h1>
			<p class="hero-sub">8,000 Monte Carlo simulations. Trained ML weights. Bayesian-smoothed matchups. Real-time 2026 data.</p>
		</div>
		<div class="stats-row">
			<div class="stat-pill">
				<span class="stat-value">{data.dashboard.historyWindow}</span>
				<span class="stat-label">History</span>
			</div>
			<div class="stat-pill">
				<span class="stat-value">{data.dashboard.season2026Matches}</span>
				<span class="stat-label">2026 Matches</span>
			</div>
			<div class="stat-pill">
				<span class="stat-value">{data.dashboard.currentPlayers}</span>
				<span class="stat-label">Players</span>
			</div>
			<div class="stat-pill">
				<span class="stat-value">{data.dashboard.venues}</span>
				<span class="stat-label">Venues</span>
			</div>
		</div>
	</section>

	<!-- Quick Fixtures -->
	{#if data.fixtures.length}
		<section class="fixtures-section">
			<h2 class="section-title">Upcoming Matches</h2>
			<div class="fixtures-scroll">
				{#each data.fixtures.slice(0, 6) as fixture}
					<button class="fixture-card" on:click={() => loadFixture(fixture)}>
						<div class="fixture-teams">{fixture.teamA} <span class="vs">vs</span> {fixture.teamB}</div>
						<div class="fixture-meta">{fixture.label}</div>
						<div class="fixture-venue">{fixture.venue}</div>
					</button>
				{/each}
			</div>
		</section>
	{/if}

	<!-- Main Controls -->
	<section id="predictor" class="controls-section">
		<div class="section-header">
			<h2 class="section-title">Match Setup</h2>
			<div class="tab-switcher">
				<button class:active={activeTab === 'predict'} on:click={() => (activeTab = 'predict')}>Winner Model</button>
				<button class:active={activeTab === 'fantasy'} on:click={() => (activeTab = 'fantasy')}>Fantasy XI</button>
			</div>
		</div>

		<form class="controls-card" on:submit|preventDefault={activeTab === 'predict' ? runPrediction : runFantasy}>
			<div class="form-row">
				<div class="form-group">
					<label for="teamA">Team A</label>
					<select id="teamA" bind:value={form.teamA}>
						{#each data.teams as team}<option value={team.name}>{team.name}</option>{/each}
					</select>
				</div>
				<div class="form-group vs-divider">
					<span>VS</span>
				</div>
				<div class="form-group">
					<label for="teamB">Team B</label>
					<select id="teamB" bind:value={form.teamB}>
						{#each data.teams as team}<option value={team.name}>{team.name}</option>{/each}
					</select>
				</div>
			</div>

			<div class="form-grid-3">
				<div class="form-group">
					<label for="venue">Venue</label>
					<select id="venue" bind:value={form.venue}>
						{#each data.venues as v}<option value={v.name}>{v.name} ({v.city})</option>{/each}
					</select>
				</div>
				<div class="form-group">
					<label for="toss">Toss Winner</label>
					<select id="toss" bind:value={form.tossWinner}>
						<option value="">Not Yet</option>
						<option value={form.teamA}>{teamASN}</option>
						<option value={form.teamB}>{teamBSN}</option>
					</select>
				</div>
				<div class="form-group">
					<label for="decision">Toss Decision</label>
					<select id="decision" bind:value={form.tossDecision} disabled={!form.tossWinner}>
						{#if !form.tossWinner}<option value="">N/A</option>{/if}
						<option value="field">Field First</option>
						<option value="bat">Bat First</option>
					</select>
				</div>
			</div>

			<div class="form-grid-3">
				<div class="form-group">
					<label for="matchTime">Match Time</label>
					<select id="matchTime" bind:value={form.matchTime}>
						<option value="afternoon">Afternoon</option>
						<option value="evening">Evening (Dew)</option>
					</select>
				</div>
				<div class="form-group">
					<label for="strategy">Fantasy Strategy</label>
					<select id="strategy" bind:value={form.strategy}>
						<option value="safe">Safe</option>
						<option value="balanced">Balanced</option>
						<option value="grand">Grand League</option>
					</select>
				</div>
				<div class="form-group">
					<label>Batting First</label>
					<div class="readonly-field">{form.battingFirst || 'Auto (venue-derived)'}</div>
				</div>
			</div>

			<!-- Impact Player Selection -->
			<div class="form-grid-2">
				<div class="form-group">
					<label for="ipA">Impact Player ({teamASN})</label>
					<select id="ipA" bind:value={form.impactPlayerA}>
						<option value="">None</option>
						{#each impactCandidatesA as p}
							<option value={p.playerKey}>{p.name} ({p.role}{p.impactMatches2026 ? ` | ${p.impactMatches2026} IP` : ''})</option>
						{/each}
					</select>
				</div>
				<div class="form-group">
					<label for="ipB">Impact Player ({teamBSN})</label>
					<select id="ipB" bind:value={form.impactPlayerB}>
						<option value="">None</option>
						{#each impactCandidatesB as p}
							<option value={p.playerKey}>{p.name} ({p.role}{p.impactMatches2026 ? ` | ${p.impactMatches2026} IP` : ''})</option>
						{/each}
					</select>
				</div>
			</div>

			<!-- Custom XI Toggle -->
			<div class="xi-toggle">
				<label class="toggle-row">
					<input type="checkbox" bind:checked={useCustomXi} />
					<span class="toggle-slider"></span>
					<span>Select Playing XI Manually</span>
				</label>
			</div>

			{#if useCustomXi}
				<div class="xi-picker-grid">
					{#each [{ side: 'A', team: form.teamA, sn: teamASN, players: teamAPlayers, selected: selectedXiA }, { side: 'B', team: form.teamB, sn: teamBSN, players: teamBPlayers, selected: selectedXiB }] as panel}
						<div class="xi-picker">
							<div class="xi-picker-head">
								<strong>{panel.sn}</strong>
								<span class="xi-count">{panel.selected.length}/11</span>
								<button type="button" class="btn-ghost-sm" on:click={() => clearXi(panel.side === 'A' ? 'A' : 'B')}>Clear</button>
							</div>
							<div class="xi-picker-list">
								{#each panel.players as p}
									<button
										type="button"
										class="xi-pick-btn"
										class:picked={panel.selected.includes(p.playerKey)}
										on:click={() => toggleXi(panel.side === 'A' ? 'A' : 'B', p.playerKey)}
									>
										<span class="role-dot" style="background: {roleColor(p.role)}">{roleIcon(p.role)}</span>
										<div class="xi-pick-info">
											<strong>{p.name}</strong>
											<small>{p.role}{p.isOverseas ? ' | OS' : ''} | {p.starts2026} starts</small>
										</div>
									</button>
								{/each}
							</div>
						</div>
					{/each}
				</div>
			{/if}

			{#if selectionError}<p class="msg-error">{selectionError}</p>{/if}

			<div class="action-row">
				<button class="btn-primary" type="submit" disabled={loadingPrediction || loadingFantasy}>
					{#if activeTab === 'predict'}
						{loadingPrediction ? 'Analyzing...' : 'Run Prediction'}
					{:else}
						{loadingFantasy ? 'Building...' : 'Build Fantasy XI'}
					{/if}
				</button>
				<button class="btn-secondary" type="button" on:click={runAll} disabled={loadingPrediction || loadingFantasy}>
					Run Both
				</button>
			</div>

			{#if error}<p class="msg-error">{error}</p>{/if}
		</form>
	</section>

	<!-- Prediction Results -->
	{#if prediction}
		<section id="results" class="results-section">
			<!-- Win Probability Hero -->
			<div class="prob-hero">
				<div class="prob-team prob-team-a">
					<span class="prob-name">{teamASN}</span>
					<span class="prob-pct" class:favored={prediction.winProbabilityA > prediction.winProbabilityB}>{pct(prediction.winProbabilityA)}</span>
					{#if prediction.monteCarloWinRateA}
						<span class="prob-mc">MC: {pct(prediction.monteCarloWinRateA)}</span>
					{/if}
				</div>

				<div class="prob-center">
					<div class="prob-bar-container">
						<div class="prob-bar-a" style="width: {prediction.winProbabilityA}%"></div>
					</div>
					{#if prediction.confidence}
						<span class="confidence-badge">Confidence: {prediction.confidence}%</span>
					{/if}
				</div>

				<div class="prob-team prob-team-b">
					<span class="prob-name">{teamBSN}</span>
					<span class="prob-pct" class:favored={prediction.winProbabilityB > prediction.winProbabilityA}>{pct(prediction.winProbabilityB)}</span>
					{#if prediction.monteCarloWinRateB}
						<span class="prob-mc">MC: {pct(prediction.monteCarloWinRateB)}</span>
					{/if}
				</div>
			</div>

			<!-- Key Stats Grid -->
			<div class="key-stats">
				<div class="key-stat">
					<span class="ks-label">Projected Scores</span>
					<span class="ks-value">{prediction.projectedScoreA} - {prediction.projectedScoreB}</span>
				</div>
				{#if prediction.eloA && prediction.eloB}
					<div class="key-stat">
						<span class="ks-label">Elo Ratings</span>
						<span class="ks-value">{prediction.eloA} vs {prediction.eloB}</span>
					</div>
				{/if}
				<div class="key-stat">
					<span class="ks-label">Batting First</span>
					<span class="ks-value">{prediction.battingFirst}</span>
				</div>
				<div class="key-stat">
					<span class="ks-label">Chase Win Rate</span>
					<span class="ks-value">{pct(prediction.venueProfile?.chaseWinRate ?? venueDetails.chaseWinRate)}</span>
				</div>
			</div>

			<!-- Impact Player Info -->
			{#if prediction.impactPlayerA || prediction.impactPlayerB}
				<div class="ip-section">
					<h3 class="subsection-title">Impact Players</h3>
					<div class="ip-grid">
						{#if prediction.impactPlayerA}
							<div class="ip-card">
								<span class="ip-team">{teamASN}</span>
								<span class="ip-role-tag" style="background: {roleColor(prediction.impactPlayerA.roleBucket)}">{roleIcon(prediction.impactPlayerA.roleBucket)}</span>
								<strong>{prediction.impactPlayerA.name}</strong>
								<small>{prediction.impactPlayerA.projectedPoints} pts | {prediction.impactPlayerA.impactMatches2026} IP appearances</small>
							</div>
						{/if}
						{#if prediction.impactPlayerB}
							<div class="ip-card">
								<span class="ip-team">{teamBSN}</span>
								<span class="ip-role-tag" style="background: {roleColor(prediction.impactPlayerB.roleBucket)}">{roleIcon(prediction.impactPlayerB.roleBucket)}</span>
								<strong>{prediction.impactPlayerB.name}</strong>
								<small>{prediction.impactPlayerB.projectedPoints} pts | {prediction.impactPlayerB.impactMatches2026} IP appearances</small>
							</div>
						{/if}
					</div>
				</div>
			{/if}

			<!-- Score Distributions -->
			{#if prediction.scoreDistA && prediction.scoreDistB}
				<div class="dist-section">
					<h3 class="subsection-title">Score Distribution (Monte Carlo)</h3>
					<div class="dist-grid">
						<div class="dist-card">
							<div class="dist-team">{teamASN}</div>
							<div class="dist-bars">
								<div class="dist-item"><span>10th %ile</span><strong>{prediction.scoreDistA.low}</strong></div>
								<div class="dist-item highlight"><span>Median</span><strong>{prediction.scoreDistA.median}</strong></div>
								<div class="dist-item"><span>90th %ile</span><strong>{prediction.scoreDistA.high}</strong></div>
							</div>
						</div>
						<div class="dist-card">
							<div class="dist-team">{teamBSN}</div>
							<div class="dist-bars">
								<div class="dist-item"><span>10th %ile</span><strong>{prediction.scoreDistB.low}</strong></div>
								<div class="dist-item highlight"><span>Median</span><strong>{prediction.scoreDistB.median}</strong></div>
								<div class="dist-item"><span>90th %ile</span><strong>{prediction.scoreDistB.high}</strong></div>
							</div>
						</div>
					</div>
				</div>
			{/if}

			<!-- Factors -->
			<div class="factors-section">
				<h3 class="subsection-title">Key Factors</h3>
				<div class="factors-grid">
					{#each prediction.factors.slice(0, 8) as factor}
						<div class="factor-card">
							<div class="factor-head">
								<strong>{factor.label}</strong>
								<span class="factor-edge" class:positive={factor.edge > 2} class:minor={factor.edge <= 2}>{factor.edge.toFixed(1)}</span>
							</div>
							<p class="factor-detail">{factor.detail}</p>
							<span class="factor-favored">Favors {factor.favoredTeam}</span>
						</div>
					{/each}
				</div>
			</div>

			<!-- Likely XI -->
			<div class="xi-section">
				<h3 class="subsection-title">Probable Playing XI</h3>
				<div class="xi-grid">
					{#each [{ label: teamASN, xi: prediction.teamAXi }, { label: teamBSN, xi: prediction.teamBXi }] as side}
						<div class="xi-card">
							<h4 class="xi-team-name">{side.label}</h4>
							<div class="xi-list">
								{#each side.xi as player, i}
									<div class="xi-player">
										<span class="xi-num">{i + 1}</span>
										<span class="xi-role-tag" style="background: {roleColor(player.roleBucket)}">{roleIcon(player.roleBucket)}</span>
										<div class="xi-player-info">
											<strong>{player.name}</strong>
											<small>{player.projectedPoints} pts{player.formScore ? ` | form ${player.formScore > 0 ? '+' : ''}${player.formScore.toFixed(1)}` : ''} | {player.starts2026} starts</small>
										</div>
										{#if player.isOverseas}<span class="os-tag">OS</span>{/if}
									</div>
								{/each}
							</div>
						</div>
					{/each}
				</div>
			</div>

			<!-- Matchups -->
			{#if prediction.matchups.length}
				<div class="matchups-section">
					<h3 class="subsection-title">Key Batter vs Bowler Matchups</h3>
					<div class="matchups-list">
						{#each prediction.matchups as m}
							<div class="matchup-card">
								<div class="matchup-names">
									<strong>{m.batter}</strong>
									<span class="matchup-vs">vs</span>
									<strong>{m.bowler}</strong>
								</div>
								<div class="matchup-stats">
									<span>{m.balls} balls</span>
									<span class="matchup-sr">{m.strikeRate} SR</span>
									<span>{m.dismissals} outs</span>
									{#if m.smoothedEdge != null}
										<span class="matchup-edge" class:positive-edge={m.smoothedEdge > 0} class:negative-edge={m.smoothedEdge < 0}>
											{m.smoothedEdge > 0 ? '+' : ''}{m.smoothedEdge.toFixed(1)}
										</span>
									{/if}
								</div>
								<span class="matchup-scope">{m.scope === 'venue' ? m.venue : 'Overall'}</span>
							</div>
						{/each}
					</div>
				</div>
			{/if}
		</section>
	{/if}

	<!-- Fantasy XI -->
	<section id="fantasy" class="fantasy-section">
		{#if fantasyError}<p class="msg-error">{fantasyError}</p>{/if}

		{#if fantasy}
			<div class="fantasy-header">
				<h2 class="section-title">Fantasy XI</h2>
				<div class="fantasy-meta-row">
					<div class="fantasy-meta">
						<span>{fantasy.totalProjectedPoints}</span>
						<small>Projected Pts</small>
					</div>
					<div class="fantasy-meta">
						<span>{fantasy.totalCredits}</span>
						<small>Credits Used</small>
					</div>
					<div class="fantasy-meta accent">
						<span>{fantasy.captain.name}</span>
						<small>Captain (C)</small>
					</div>
					<div class="fantasy-meta accent-alt">
						<span>{fantasy.viceCaptain.name}</span>
						<small>Vice Captain (VC)</small>
					</div>
				</div>
			</div>

			<div class="fantasy-grid">
				{#each fantasy.lineup as player}
					<div
						class="fantasy-player-card"
						class:captain-card={player.playerKey === fantasy.captain.playerKey}
						class:vc-card={player.playerKey === fantasy.viceCaptain.playerKey}
					>
						{#if player.playerKey === fantasy.captain.playerKey}
							<span class="cap-badge">C</span>
						{:else if player.playerKey === fantasy.viceCaptain.playerKey}
							<span class="cap-badge vc-badge">VC</span>
						{/if}
						<span class="f-role-tag" style="background: {roleColor(player.roleBucket)}">{roleIcon(player.roleBucket)}</span>
						<strong class="f-name">{player.name}</strong>
						<small class="f-team">{player.currentTeam}</small>
						<div class="f-stats">
							<span>{player.projectedPoints} pts</span>
							<span>{player.fantasyCredit} cr</span>
						</div>
					</div>
				{/each}
			</div>

			<div class="fantasy-notes">
				{#each fantasy.notes as note}
					<p>{note}</p>
				{/each}
			</div>
		{:else if !loadingFantasy}
			<div class="empty-state">
				<h3>Fantasy XI will appear after prediction</h3>
				<p>Set up the match context above and click "Run Both".</p>
			</div>
		{/if}
	</section>
</div>

<style>
	/* ─── Page Container ─── */
	.page {
		max-width: var(--max-width);
		margin: 0 auto;
		padding: 0 1rem 3rem;
		display: grid;
		gap: 1.5rem;
	}

	/* ─── Hero ─── */
	.hero {
		text-align: center;
		padding: 3rem 1rem 2rem;
		background: var(--gradient-surface);
		border-radius: var(--radius-xl);
		border: 1px solid var(--border-color);
		margin-top: 1rem;
	}

	.hero-badge {
		display: inline-block;
		padding: 0.3rem 0.9rem;
		border-radius: var(--radius-full);
		background: var(--gradient-primary);
		color: #fff;
		font-size: 0.78rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		margin-bottom: 0.8rem;
	}

	.hero h1 {
		font-family: var(--font-display);
		font-size: clamp(1.6rem, 4vw, 2.4rem);
		font-weight: 700;
		margin: 0 0 0.6rem;
		color: var(--text-primary);
		line-height: 1.15;
	}

	.hero-sub {
		color: var(--text-secondary);
		font-size: 0.95rem;
		max-width: 600px;
		margin: 0 auto;
		line-height: 1.5;
	}

	.stats-row {
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		gap: 0.6rem;
		margin-top: 1.5rem;
	}

	.stat-pill {
		display: flex;
		flex-direction: column;
		align-items: center;
		padding: 0.6rem 1.2rem;
		background: var(--bg-card);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-lg);
		min-width: 0;
		flex: 1 1 auto;
	}

	.stat-value {
		font-family: var(--font-display);
		font-weight: 700;
		font-size: 1.1rem;
		color: var(--text-accent);
	}

	.stat-label {
		font-size: 0.72rem;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}

	/* ─── Fixtures ─── */
	.fixtures-section { padding: 0 0.25rem; }

	.section-title {
		font-family: var(--font-display);
		font-size: 1.2rem;
		font-weight: 700;
		color: var(--text-primary);
		margin: 0 0 0.8rem;
	}

	.fixtures-scroll {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
		gap: 0.6rem;
	}

	.fixture-card {
		background: var(--bg-card);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-lg);
		padding: 0.9rem 1rem;
		text-align: left;
		cursor: pointer;
		transition: all 0.2s ease;
		font: inherit;
		color: inherit;
	}

	.fixture-card:hover {
		background: var(--bg-card-hover);
		border-color: var(--border-active);
		box-shadow: var(--shadow-glow);
	}

	.fixture-teams {
		font-weight: 700;
		font-size: 0.92rem;
		color: var(--text-primary);
	}

	.vs { color: var(--text-muted); font-weight: 400; }

	.fixture-meta {
		font-size: 0.8rem;
		color: var(--text-secondary);
		margin-top: 0.2rem;
	}

	.fixture-venue {
		font-size: 0.75rem;
		color: var(--text-muted);
		margin-top: 0.15rem;
	}

	/* ─── Section Header with Tabs ─── */
	.section-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 0.8rem;
		margin-bottom: 0.8rem;
	}

	.tab-switcher {
		display: flex;
		background: var(--bg-card);
		border-radius: var(--radius-full);
		padding: 0.2rem;
		border: 1px solid var(--border-color);
	}

	.tab-switcher button {
		padding: 0.4rem 1rem;
		border: none;
		border-radius: var(--radius-full);
		background: transparent;
		color: var(--text-secondary);
		font: inherit;
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
		transition: all 0.2s ease;
	}

	.tab-switcher button.active {
		background: var(--gradient-primary);
		color: #fff;
	}

	/* ─── Controls Card ─── */
	.controls-card {
		background: var(--bg-card);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-xl);
		padding: 1.5rem;
	}

	.form-row {
		display: grid;
		grid-template-columns: 1fr auto 1fr;
		gap: 0.8rem;
		align-items: end;
		margin-bottom: 1rem;
	}

	.vs-divider {
		display: grid;
		place-items: center;
		padding-bottom: 0.3rem;
	}

	.vs-divider span {
		font-family: var(--font-display);
		font-weight: 700;
		font-size: 0.9rem;
		color: var(--text-muted);
		background: var(--bg-surface);
		padding: 0.35rem 0.7rem;
		border-radius: var(--radius-full);
	}

	.form-grid-3 {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 0.8rem;
		margin-bottom: 1rem;
	}

	.form-group {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		min-width: 0;
	}

	.form-group label {
		font-size: 0.78rem;
		font-weight: 600;
		color: var(--text-secondary);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	select, .readonly-field {
		padding: 0.65rem 0.8rem;
		border-radius: var(--radius-md);
		border: 1px solid var(--border-color);
		background: var(--bg-input);
		color: var(--text-primary);
		font: inherit;
		font-size: 0.9rem;
		transition: border-color 0.2s ease;
		appearance: auto;
		width: 100%;
		min-width: 0;
		max-width: 100%;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	select:focus {
		outline: none;
		border-color: var(--accent-primary);
		box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2);
	}

	.readonly-field {
		background: var(--bg-surface);
		color: var(--text-secondary);
	}

	/* ─── Toggle ─── */
	.xi-toggle {
		padding: 1rem 0 0.5rem;
		border-top: 1px solid var(--border-color);
		margin-top: 0.5rem;
	}

	.toggle-row {
		display: flex;
		align-items: center;
		gap: 0.7rem;
		cursor: pointer;
		font-weight: 600;
		font-size: 0.9rem;
		color: var(--text-secondary);
	}

	.toggle-row input { display: none; }

	.toggle-slider {
		position: relative;
		width: 40px;
		height: 22px;
		background: var(--bg-surface);
		border-radius: var(--radius-full);
		transition: background 0.2s;
		flex-shrink: 0;
	}

	.toggle-slider::after {
		content: '';
		position: absolute;
		top: 3px;
		left: 3px;
		width: 16px;
		height: 16px;
		border-radius: 50%;
		background: var(--text-muted);
		transition: all 0.2s;
	}

	.toggle-row input:checked + .toggle-slider {
		background: var(--accent-primary);
	}

	.toggle-row input:checked + .toggle-slider::after {
		left: 21px;
		background: #fff;
	}

	/* ─── XI Picker ─── */
	.xi-picker-grid {
		display: grid;
		grid-template-columns: repeat(2, 1fr);
		gap: 0.8rem;
		margin-top: 0.8rem;
	}

	.xi-picker {
		background: var(--bg-surface);
		border-radius: var(--radius-lg);
		padding: 0.8rem;
	}

	.xi-picker-head {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-bottom: 0.6rem;
	}

	.xi-picker-head strong { color: var(--text-primary); }

	.xi-count {
		font-size: 0.78rem;
		color: var(--text-accent);
		font-weight: 600;
		margin-left: auto;
	}

	.btn-ghost-sm {
		padding: 0.25rem 0.6rem;
		border: 1px solid var(--border-color);
		border-radius: var(--radius-full);
		background: transparent;
		color: var(--text-muted);
		font-size: 0.75rem;
		cursor: pointer;
		font: inherit;
	}

	.xi-picker-list {
		display: grid;
		gap: 0.35rem;
		max-height: 16rem;
		overflow-y: auto;
		padding-right: 0.2rem;
	}

	.xi-picker-list::-webkit-scrollbar { width: 4px; }
	.xi-picker-list::-webkit-scrollbar-thumb { background: var(--border-color); border-radius: 2px; }

	.xi-pick-btn {
		display: flex;
		align-items: center;
		gap: 0.55rem;
		padding: 0.5rem 0.6rem;
		border: 1px solid var(--border-color);
		border-radius: var(--radius-md);
		background: var(--bg-card);
		color: var(--text-primary);
		cursor: pointer;
		text-align: left;
		font: inherit;
		transition: all 0.15s ease;
	}

	.xi-pick-btn:hover { border-color: var(--border-active); }

	.xi-pick-btn.picked {
		background: rgba(59, 130, 246, 0.12);
		border-color: var(--accent-primary);
	}

	.role-dot {
		display: grid;
		place-items: center;
		width: 28px;
		height: 28px;
		border-radius: var(--radius-sm);
		color: #fff;
		font-size: 0.6rem;
		font-weight: 700;
		flex-shrink: 0;
	}

	.xi-pick-info strong { font-size: 0.85rem; display: block; }
	.xi-pick-info small { font-size: 0.72rem; color: var(--text-muted); }

	/* ─── Actions ─── */
	.action-row {
		display: flex;
		gap: 0.6rem;
		margin-top: 1rem;
	}

	.btn-primary, .btn-secondary {
		padding: 0.7rem 1.5rem;
		border: none;
		border-radius: var(--radius-full);
		font: inherit;
		font-weight: 700;
		font-size: 0.9rem;
		cursor: pointer;
		transition: all 0.2s ease;
	}

	.btn-primary {
		background: var(--gradient-primary);
		color: #fff;
		flex: 1;
	}

	.btn-primary:hover:not(:disabled) { box-shadow: var(--shadow-glow); transform: translateY(-1px); }
	.btn-primary:disabled, .btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }

	.btn-secondary {
		background: var(--bg-surface);
		color: var(--text-secondary);
		border: 1px solid var(--border-color);
	}

	.btn-secondary:hover:not(:disabled) { border-color: var(--border-active); }

	.msg-error {
		padding: 0.7rem 1rem;
		border-radius: var(--radius-md);
		background: rgba(239, 68, 68, 0.12);
		border: 1px solid rgba(239, 68, 68, 0.25);
		color: #fca5a5;
		font-size: 0.88rem;
		margin: 0.5rem 0 0;
	}

	/* ─── Probability Hero ─── */
	.prob-hero {
		display: grid;
		grid-template-columns: 1fr 2fr 1fr;
		gap: 1rem;
		align-items: center;
		padding: 1.5rem;
		background: var(--bg-card);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-xl);
	}

	.prob-team {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}

	.prob-team-b { text-align: right; }

	.prob-name {
		font-family: var(--font-display);
		font-weight: 700;
		font-size: 1rem;
		color: var(--text-secondary);
	}

	.prob-pct {
		font-family: var(--font-display);
		font-weight: 700;
		font-size: 2rem;
		line-height: 1;
		color: var(--text-muted);
	}

	.prob-pct.favored { color: var(--accent-green); }

	.prob-mc {
		font-size: 0.75rem;
		color: var(--text-muted);
		font-style: italic;
	}

	.prob-center { display: flex; flex-direction: column; gap: 0.5rem; align-items: center; }

	.prob-bar-container {
		width: 100%;
		height: 8px;
		border-radius: var(--radius-full);
		background: var(--bg-surface);
		overflow: hidden;
	}

	.prob-bar-a {
		height: 100%;
		border-radius: var(--radius-full);
		background: var(--gradient-green);
		transition: width 0.4s ease;
	}

	.confidence-badge {
		font-size: 0.72rem;
		font-weight: 600;
		color: var(--text-muted);
		padding: 0.2rem 0.6rem;
		background: var(--bg-surface);
		border-radius: var(--radius-full);
	}

	/* ─── Key Stats ─── */
	.key-stats {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
		gap: 0.6rem;
	}

	.key-stat {
		background: var(--bg-card);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-lg);
		padding: 0.8rem;
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
	}

	.ks-label { font-size: 0.72rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }
	.ks-value { font-family: var(--font-display); font-weight: 700; font-size: 0.95rem; color: var(--text-primary); }

	/* ─── Score Distribution ─── */
	.dist-grid {
		display: grid;
		grid-template-columns: repeat(2, 1fr);
		gap: 0.6rem;
	}

	.dist-card {
		background: var(--bg-card);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-lg);
		padding: 1rem;
	}

	.dist-team {
		font-family: var(--font-display);
		font-weight: 700;
		color: var(--text-secondary);
		margin-bottom: 0.6rem;
	}

	.dist-bars { display: flex; gap: 0.5rem; }

	.dist-item {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		padding: 0.5rem;
		border-radius: var(--radius-md);
		background: var(--bg-surface);
	}

	.dist-item span { font-size: 0.68rem; color: var(--text-muted); }
	.dist-item strong { font-family: var(--font-display); font-size: 1.1rem; color: var(--text-primary); }

	.dist-item.highlight {
		background: rgba(59, 130, 246, 0.12);
		border: 1px solid rgba(59, 130, 246, 0.25);
	}

	.dist-item.highlight strong { color: var(--text-accent); }

	/* ─── Subsection Title ─── */
	.subsection-title {
		font-family: var(--font-display);
		font-size: 1rem;
		font-weight: 700;
		color: var(--text-secondary);
		margin: 0 0 0.7rem;
	}

	/* ─── Factors ─── */
	.factors-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
		gap: 0.5rem;
	}

	.factor-card {
		background: var(--bg-card);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-lg);
		padding: 0.8rem;
	}

	.factor-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
	}

	.factor-head strong { font-size: 0.85rem; color: var(--text-primary); }

	.factor-edge {
		font-family: var(--font-display);
		font-size: 0.78rem;
		font-weight: 700;
		padding: 0.15rem 0.45rem;
		border-radius: var(--radius-full);
	}

	.factor-edge.positive { background: rgba(16, 185, 129, 0.15); color: var(--accent-green); }
	.factor-edge.minor { background: var(--bg-surface); color: var(--text-muted); }

	.factor-detail {
		font-size: 0.78rem;
		color: var(--text-muted);
		margin: 0.3rem 0 0.25rem;
		line-height: 1.4;
	}

	.factor-favored {
		font-size: 0.72rem;
		color: var(--text-accent);
		font-weight: 600;
	}

	/* ─── Likely XI ─── */
	.xi-grid {
		display: grid;
		grid-template-columns: repeat(2, 1fr);
		gap: 0.6rem;
	}

	.xi-card {
		background: var(--bg-card);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-xl);
		padding: 1rem;
	}

	.xi-team-name {
		font-family: var(--font-display);
		font-weight: 700;
		color: var(--text-accent);
		margin: 0 0 0.6rem;
		font-size: 1rem;
	}

	.xi-list { display: flex; flex-direction: column; gap: 0.3rem; }

	.xi-player {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.45rem 0.5rem;
		border-radius: var(--radius-md);
		transition: background 0.15s ease;
	}

	.xi-player:hover { background: var(--bg-surface); }

	.xi-num {
		font-size: 0.72rem;
		color: var(--text-muted);
		width: 1.2rem;
		text-align: center;
		flex-shrink: 0;
	}

	.xi-role-tag {
		display: grid;
		place-items: center;
		width: 26px;
		height: 26px;
		border-radius: var(--radius-sm);
		color: #fff;
		font-size: 0.58rem;
		font-weight: 700;
		flex-shrink: 0;
	}

	.xi-player-info { flex: 1; min-width: 0; }
	.xi-player-info strong { font-size: 0.88rem; display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.xi-player-info small { font-size: 0.72rem; color: var(--text-muted); }

	.os-tag {
		font-size: 0.6rem;
		font-weight: 700;
		padding: 0.1rem 0.35rem;
		border-radius: var(--radius-sm);
		background: rgba(245, 158, 11, 0.2);
		color: var(--accent-orange);
	}

	/* ─── Matchups ─── */
	.matchups-list {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
		gap: 0.5rem;
	}

	.matchup-card {
		background: var(--bg-card);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-lg);
		padding: 0.8rem;
	}

	.matchup-names {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		flex-wrap: wrap;
	}

	.matchup-names strong { font-size: 0.88rem; }
	.matchup-vs { color: var(--text-muted); font-size: 0.78rem; }

	.matchup-stats {
		display: flex;
		gap: 0.6rem;
		margin-top: 0.35rem;
		font-size: 0.78rem;
		color: var(--text-muted);
	}

	.matchup-sr { color: var(--text-accent); font-weight: 600; }

	.matchup-edge {
		font-weight: 700;
		padding: 0 0.3rem;
		border-radius: var(--radius-sm);
	}

	.positive-edge { color: var(--accent-green); background: rgba(16, 185, 129, 0.1); }
	.negative-edge { color: var(--accent-red); background: rgba(239, 68, 68, 0.1); }

	.matchup-scope { font-size: 0.68rem; color: var(--text-muted); font-style: italic; }

	/* ─── Fantasy Section ─── */
	.fantasy-header { margin-bottom: 1rem; }

	.fantasy-meta-row {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
		gap: 0.5rem;
		margin-top: 0.8rem;
	}

	.fantasy-meta {
		background: var(--bg-card);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-lg);
		padding: 0.8rem;
		text-align: center;
		min-width: 0;
		overflow: hidden;
	}

	.fantasy-meta span {
		display: block;
		font-family: var(--font-display);
		font-weight: 700;
		font-size: 1rem;
		color: var(--text-primary);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.fantasy-meta small { font-size: 0.72rem; color: var(--text-muted); }
	.fantasy-meta.accent { border-color: rgba(59, 130, 246, 0.3); }
	.fantasy-meta.accent span { color: var(--text-accent); }
	.fantasy-meta.accent-alt { border-color: rgba(139, 92, 246, 0.3); }
	.fantasy-meta.accent-alt span { color: var(--accent-secondary); }

	.fantasy-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
		gap: 0.5rem;
	}

	.fantasy-player-card {
		position: relative;
		background: var(--bg-card);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-lg);
		padding: 0.9rem 0.7rem;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.25rem;
		text-align: center;
		transition: all 0.2s ease;
	}

	.fantasy-player-card:hover { background: var(--bg-card-hover); }

	.captain-card { border-color: var(--accent-primary); box-shadow: 0 0 12px rgba(59, 130, 246, 0.15); }
	.vc-card { border-color: var(--accent-secondary); box-shadow: 0 0 12px rgba(139, 92, 246, 0.12); }

	.cap-badge {
		position: absolute;
		top: -6px;
		right: -6px;
		width: 22px;
		height: 22px;
		display: grid;
		place-items: center;
		border-radius: 50%;
		background: var(--accent-primary);
		color: #fff;
		font-size: 0.62rem;
		font-weight: 800;
	}

	.vc-badge { background: var(--accent-secondary); }

	.f-role-tag {
		display: grid;
		place-items: center;
		width: 30px;
		height: 30px;
		border-radius: var(--radius-md);
		color: #fff;
		font-size: 0.6rem;
		font-weight: 700;
	}

	.f-name { font-size: 0.85rem; color: var(--text-primary); }
	.f-team { font-size: 0.72rem; color: var(--text-muted); }

	.f-stats {
		display: flex;
		gap: 0.5rem;
		font-size: 0.72rem;
		color: var(--text-secondary);
	}

	.fantasy-notes {
		margin-top: 1rem;
		padding: 0.8rem;
		background: var(--bg-card);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-lg);
	}

	.fantasy-notes p {
		margin: 0 0 0.3rem;
		font-size: 0.78rem;
		color: var(--text-muted);
		line-height: 1.5;
	}

	.fantasy-notes p:last-child { margin-bottom: 0; }

	/* ─── Empty State ─── */
	.empty-state {
		text-align: center;
		padding: 3rem 1rem;
		color: var(--text-muted);
	}

	.empty-state h3 {
		font-family: var(--font-display);
		margin: 0 0 0.4rem;
		color: var(--text-secondary);
	}

	.empty-state p { margin: 0; font-size: 0.9rem; }

	/* ─── Results section ─── */
	.results-section { display: grid; gap: 1.2rem; }

	/* ─── Impact Player Cards ─── */
	.ip-grid {
		display: grid;
		grid-template-columns: repeat(2, 1fr);
		gap: 0.6rem;
	}

	.ip-card {
		background: var(--bg-card);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-lg);
		padding: 0.9rem;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.3rem;
		text-align: center;
	}

	.ip-card strong { font-size: 0.92rem; color: var(--text-primary); }
	.ip-card small { font-size: 0.75rem; color: var(--text-muted); }

	.ip-team {
		font-size: 0.72rem;
		font-weight: 700;
		color: var(--text-accent);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	.ip-role-tag {
		display: grid;
		place-items: center;
		width: 28px;
		height: 28px;
		border-radius: var(--radius-sm);
		color: #fff;
		font-size: 0.6rem;
		font-weight: 700;
	}

	/* ─── Form Grid 2-col ─── */
	.form-grid-2 {
		display: grid;
		grid-template-columns: repeat(2, 1fr);
		gap: 0.8rem;
		margin-bottom: 1rem;
	}

	/* ─── Responsive ─── */
	@media (max-width: 768px) {
		.page {
			padding: 0 0.5rem 2rem;
			gap: 1rem;
		}

		.hero {
			padding: 1.5rem 0.75rem 1.2rem;
			margin-top: 0.5rem;
		}

		.hero h1 { font-size: 1.35rem; }

		.hero-sub {
			font-size: 0.82rem;
			line-height: 1.45;
		}

		.hero-badge {
			font-size: 0.7rem;
			padding: 0.25rem 0.7rem;
			margin-bottom: 0.5rem;
		}

		.stats-row {
			gap: 0.35rem;
			margin-top: 1rem;
			display: grid;
			grid-template-columns: repeat(2, 1fr);
		}

		.stat-pill {
			min-width: 0;
			padding: 0.45rem 0.5rem;
		}

		.stat-value { font-size: 0.9rem; }
		.stat-label { font-size: 0.65rem; }

		/* Fixtures */
		.fixtures-section { padding: 0; }

		.fixtures-scroll {
			grid-template-columns: 1fr;
			gap: 0.4rem;
		}

		.fixture-card {
			padding: 0.7rem 0.8rem;
		}

		.fixture-teams { font-size: 0.85rem; }
		.fixture-venue {
			font-size: 0.7rem;
			white-space: nowrap;
			overflow: hidden;
			text-overflow: ellipsis;
		}

		/* Section header and tabs */
		.section-header {
			flex-direction: column;
			align-items: flex-start;
			gap: 0.5rem;
		}

		.section-title { font-size: 1.05rem; }

		.tab-switcher {
			width: 100%;
			display: grid;
			grid-template-columns: 1fr 1fr;
		}

		.tab-switcher button {
			padding: 0.4rem 0.5rem;
			font-size: 0.8rem;
			text-align: center;
		}

		/* Controls card */
		.controls-card {
			padding: 0.9rem 0.7rem;
			border-radius: var(--radius-lg);
		}

		.form-row {
			grid-template-columns: 1fr;
			gap: 0.6rem;
		}

		.vs-divider { padding: 0; }
		.vs-divider span { display: none; }

		.form-grid-3 {
			grid-template-columns: 1fr;
			gap: 0.6rem;
		}

		.form-grid-2 {
			grid-template-columns: 1fr;
			gap: 0.6rem;
		}

		.form-group label {
			font-size: 0.72rem;
		}

		select, .readonly-field {
			padding: 0.55rem 0.65rem;
			font-size: 0.85rem;
		}

		/* XI pickers */
		.xi-picker-grid {
			grid-template-columns: 1fr;
		}

		.xi-picker {
			padding: 0.6rem;
		}

		.xi-picker-list {
			max-height: 14rem;
		}

		.xi-pick-btn {
			padding: 0.4rem 0.5rem;
			gap: 0.4rem;
		}

		.xi-pick-info strong { font-size: 0.8rem; }
		.xi-pick-info small { font-size: 0.68rem; }

		.role-dot {
			width: 24px;
			height: 24px;
			font-size: 0.55rem;
		}

		/* Action buttons */
		.action-row {
			flex-direction: column;
		}

		.btn-primary, .btn-secondary {
			padding: 0.6rem 1rem;
			font-size: 0.85rem;
			width: 100%;
		}

		/* Probability hero */
		.prob-hero {
			grid-template-columns: 1fr;
			text-align: center;
			gap: 0.5rem;
			padding: 1rem 0.8rem;
		}

		.prob-team {
			display: flex;
			flex-direction: row;
			align-items: center;
			justify-content: center;
			gap: 0.5rem;
		}

		.prob-team-b { text-align: center; }

		.prob-name { font-size: 0.9rem; }

		.prob-pct { font-size: 1.5rem; }

		.prob-mc { font-size: 0.7rem; }

		/* Key stats */
		.key-stats {
			grid-template-columns: repeat(2, 1fr);
			gap: 0.4rem;
		}

		.key-stat {
			padding: 0.6rem;
		}

		.ks-label { font-size: 0.65rem; }
		.ks-value { font-size: 0.85rem; }

		/* Score distributions */
		.dist-grid {
			grid-template-columns: 1fr;
		}

		.dist-card { padding: 0.8rem; }

		.dist-bars { gap: 0.3rem; }

		.dist-item {
			padding: 0.4rem 0.3rem;
		}

		.dist-item span { font-size: 0.62rem; }
		.dist-item strong { font-size: 0.95rem; }

		/* Factors */
		.factors-grid {
			grid-template-columns: 1fr;
		}

		.factor-card { padding: 0.6rem; }
		.factor-head strong { font-size: 0.8rem; }
		.factor-detail { font-size: 0.72rem; }

		/* XI display */
		.xi-grid {
			grid-template-columns: 1fr;
		}

		.xi-card {
			padding: 0.8rem;
		}

		.xi-team-name { font-size: 0.9rem; }

		.xi-player {
			padding: 0.35rem 0.3rem;
			gap: 0.35rem;
		}

		.xi-role-tag {
			width: 22px;
			height: 22px;
			font-size: 0.52rem;
		}

		.xi-player-info strong { font-size: 0.82rem; }
		.xi-player-info small { font-size: 0.68rem; }

		/* Matchups */
		.matchups-list {
			grid-template-columns: 1fr;
		}

		.matchup-card { padding: 0.6rem; }
		.matchup-names strong { font-size: 0.82rem; }
		.matchup-stats { font-size: 0.72rem; gap: 0.4rem; }

		/* Impact player grid */
		.ip-grid {
			grid-template-columns: 1fr;
		}

		.ip-card {
			padding: 0.7rem;
		}

		/* Fantasy section */
		.fantasy-meta-row {
			grid-template-columns: repeat(2, 1fr);
			gap: 0.4rem;
		}

		.fantasy-meta {
			padding: 0.6rem 0.4rem;
		}

		.fantasy-meta span { font-size: 0.88rem; }
		.fantasy-meta small { font-size: 0.68rem; }

		.fantasy-grid {
			grid-template-columns: repeat(2, 1fr);
			gap: 0.4rem;
		}

		.fantasy-player-card {
			padding: 0.7rem 0.5rem;
			gap: 0.2rem;
		}

		.f-role-tag {
			width: 26px;
			height: 26px;
			font-size: 0.55rem;
		}

		.f-name { font-size: 0.78rem; }
		.f-team { font-size: 0.68rem; }
		.f-stats { font-size: 0.68rem; gap: 0.3rem; }

		.fantasy-notes p { font-size: 0.72rem; }

		/* Results section */
		.results-section { gap: 0.8rem; }

		/* Empty state */
		.empty-state { padding: 2rem 0.5rem; }
		.empty-state h3 { font-size: 1rem; }
		.empty-state p { font-size: 0.82rem; }

		/* Subsection */
		.subsection-title { font-size: 0.9rem; }
	}

	@media (max-width: 400px) {
		.page {
			padding: 0 0.35rem 1.5rem;
		}

		.hero {
			padding: 1.2rem 0.5rem 1rem;
		}

		.hero h1 { font-size: 1.2rem; }

		.stats-row {
			grid-template-columns: repeat(2, 1fr);
		}

		.prob-pct { font-size: 1.3rem; }

		.fantasy-grid {
			grid-template-columns: 1fr;
		}

		.fantasy-meta-row {
			grid-template-columns: repeat(2, 1fr);
		}

		.controls-card {
			padding: 0.7rem 0.5rem;
		}

		select, .readonly-field {
			padding: 0.5rem 0.55rem;
			font-size: 0.82rem;
		}

		.fixture-teams { font-size: 0.8rem; }

		.key-stats {
			grid-template-columns: 1fr 1fr;
		}

		.ks-value {
			font-size: 0.78rem;
			word-break: break-word;
		}
	}

	@media (max-width: 340px) {
		.stats-row {
			grid-template-columns: 1fr 1fr;
		}

		.stat-pill {
			padding: 0.35rem 0.3rem;
		}

		.stat-value { font-size: 0.82rem; }
		.stat-label { font-size: 0.6rem; }
	}
</style>

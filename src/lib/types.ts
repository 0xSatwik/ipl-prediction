export type Role = 'Wicketkeeper' | 'Batter' | 'All-Rounder' | 'Bowler';

export type VenueHighlight = {
	venue: string;
	battingScore: number;
	bowlingScore: number;
	sample: number;
};

export type PlayerBatting = {
	innings: number;
	runs: number;
	balls: number;
	outs: number;
	average: number;
	strikeRate: number;
	boundaryRate: number;
	index: number;
	recentIndex: number;
	chaseIndex: number;
	batFirstIndex: number;
	powerplayIndex: number;
	middleIndex: number;
	deathIndex: number;
};

export type PlayerBowling = {
	innings: number;
	balls: number;
	wickets: number;
	overs: number;
	average: number;
	economy: number;
	strikeRate: number;
	dotRate: number;
	index: number;
	recentIndex: number;
	powerplayIndex: number;
	middleIndex: number;
	deathIndex: number;
};

export type PlayerProfile = {
	name: string;
	playerKey: string;
	slug: string;
	role: Role;
	roleLabel: string;
	currentTeam: string;
	currentTeamId: string;
	teamShortName: string;
	teamColors: string[];
	profileUrl: string;
	imageUrl: string;
	isCaptain: boolean;
	isOverseas: boolean;
	nationality: string;
	bats: string;
	bowls: string;
	matchesPlayed: number;
	batting: PlayerBatting;
	bowling: PlayerBowling;
	venueHighlights: VenueHighlight[];
	selectionScore: number;
	availability: number;
	baseFantasyPoints: number;
	fantasyCredit: number;
	matches2026: number;
	starts2026: number;
	impactMatches2026: number;
	recentStarts2026: number;
	lastSeen: string;
	recencyScore: number;
	formScore: number;
	expectedBattingPosition: number;
	expectedBallsFaced: number;
	expectedOversBowled: number;
};

export type TeamVenueStat = {
	venue: string;
	matches: number;
	winRate: number;
};

export type TeamProfile = {
	id: string;
	name: string;
	shortName: string;
	slug: string;
	colors: string[];
	matches: number;
	wins: number;
	winRate: number;
	battingRating: number;
	bowlingRating: number;
	eloRating: number;
	venueStats: TeamVenueStat[];
	squad: PlayerProfile[];
};

export type VenueSpotlight = {
	name: string;
	team: string;
	role: Role;
	battingScore: number;
	bowlingScore: number;
	combined: number;
};

export type VenueProfile = {
	name: string;
	slug: string;
	city: string;
	matches: number;
	firstInningsAverage: number;
	chaseWinRate: number;
	topBatters: VenueSpotlight[];
	topBowlers: VenueSpotlight[];
};

export type PairStat = {
	batterKey: string;
	bowlerKey: string;
	batter: string;
	bowler: string;
	balls: number;
	runs: number;
	dismissals: number;
	strikeRate: number;
	dismissalRate: number;
	edge: number;
	smoothedEdge: number;
	scope: 'overall' | 'venue';
	venue?: string | null;
};

export type HeadToHead = {
	teamA: string;
	teamB: string;
	matches: number;
	winsA: number;
	winsB: number;
	winRateA: number;
	winRateB: number;
	recentMatches: number;
	recentWinsA: number;
	recentWinsB: number;
	decayedWinRateA: number;
	decayedWinRateB: number;
};

export type DashboardSummary = {
	matches: number;
	season2026Matches: number;
	seasons: number;
	venues: number;
	currentPlayers: number;
	historyWindow: string;
};

export type Fixture = {
	matchId: number;
	date: string;
	label: string;
	teamA: string;
	teamB: string;
	venue: string;
	city: string;
	status: string;
};

export type ModelWeights = {
	intercept: number;
	battingEdge: number;
	bowlingEdge: number;
	venueEdge: number;
	eloEdge: number;
	formEdge: number;
	tossEdge: number;
	h2hEdge: number;
	stabilityEdge: number;
	freshnessEdge: number;
	matchupEdge: number;
	deathBowlingEdge: number;
	powerplayEdge: number;
};

export type GlobalPriors = {
	battingIndex: number;
	bowlingIndex: number;
	firstInningsAverage: number;
	chaseWinRate: number;
	pairEdge: number;
	priorStrength: number;
};

export type ScoreDistribution = {
	low: number;
	median: number;
	high: number;
};

export type AppData = {
	builtAt: string;
	source: {
		historical: string;
		currentSquads: string;
		notes: string[];
	};
	dashboard: DashboardSummary;
	fixtures: Fixture[];
	teams: TeamProfile[];
	players: PlayerProfile[];
	venues: VenueProfile[];
	pairStats: PairStat[];
	headToHead: HeadToHead[];
	modelWeights: ModelWeights;
	globalPriors: GlobalPriors;
};

export type PredictionInput = {
	teamA: string;
	teamB: string;
	venue: string;
	tossWinner?: string;
	tossDecision?: 'bat' | 'field';
	battingFirst?: string;
	strategy?: 'safe' | 'balanced' | 'grand';
	selectedXiA?: string[];
	selectedXiB?: string[];
	matchTime?: 'afternoon' | 'evening';
	impactPlayerA?: string;
	impactPlayerB?: string;
};

export type MatchFactor = {
	label: string;
	detail: string;
	edge: number;
	favoredTeam: string;
};

export type XiPlayer = PlayerProfile & {
	contextScore: number;
	projectedPoints: number;
	battingProjection: number;
	bowlingProjection: number;
	floorPoints: number;
	ceilingPoints: number;
	volatility: number;
	consistencyScore: number;
	roleBucket: Role;
};

export type PredictionResult = {
	teamA: string;
	teamB: string;
	venue: string;
	battingFirst: string;
	tossWinner?: string;
	tossDecision?: string;
	matchTime?: 'afternoon' | 'evening';
	winProbabilityA: number;
	winProbabilityB: number;
	monteCarloWinRateA: number;
	monteCarloWinRateB: number;
	projectedScoreA: number;
	projectedScoreB: number;
	scoreDistA: ScoreDistribution;
	scoreDistB: ScoreDistribution;
	eloA: number;
	eloB: number;
	confidence: number;
	teamAXi: XiPlayer[];
	teamBXi: XiPlayer[];
	impactPlayerA?: XiPlayer;
	impactPlayerB?: XiPlayer;
	factors: MatchFactor[];
	matchups: PairStat[];
	venueProfile?: VenueProfile;
};

export type FantasyResult = {
	teamA: string;
	teamB: string;
	venue: string;
	strategy: 'safe' | 'balanced' | 'grand';
	lineup: XiPlayer[];
	captain: XiPlayer;
	viceCaptain: XiPlayer;
	totalProjectedPoints: number;
	totalCredits: number;
	notes: string[];
};

<svelte:options runes={false} />

<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';

	let mobileMenuOpen = false;

	function closeMenu() {
		mobileMenuOpen = false;
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<div class="app">
	<header class="navbar">
		<div class="navbar-inner">
			<a class="logo" href="/" on:click={closeMenu}>
				<span class="logo-icon">
					<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
						<circle cx="12" cy="12" r="10" />
						<path d="M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20M2 12h20" />
					</svg>
				</span>
				<div class="logo-text">
					<strong>IPL Predictor</strong>
					<small>ML-powered match analysis</small>
				</div>
			</a>

			<button
				class="hamburger"
				class:active={mobileMenuOpen}
				on:click={() => (mobileMenuOpen = !mobileMenuOpen)}
				aria-label="Toggle menu"
			>
				<span></span>
				<span></span>
				<span></span>
			</button>

			<nav class="nav-links" class:open={mobileMenuOpen}>
				<a href="#predictor" on:click={closeMenu}>Predict</a>
				<a href="#results" on:click={closeMenu}>Likely XI</a>
				<a href="#fantasy" on:click={closeMenu}>Fantasy</a>
			</nav>
		</div>
	</header>

	<main>
		<slot />
	</main>

	<footer class="site-footer">
		<p>IPL Match Predictor &mdash; powered by Kaggle history, official IPL feeds, and trained ML models.</p>
	</footer>
</div>

{#if mobileMenuOpen}
	<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
	<div class="overlay" on:click={closeMenu}></div>
{/if}

<style>
	/* ─── CSS Custom Properties (Design Tokens) ─── */
	:global(:root) {
		--bg-primary: #0a0f1a;
		--bg-secondary: #111827;
		--bg-card: #1a2332;
		--bg-card-hover: #1f2b3d;
		--bg-surface: #243044;
		--bg-input: #0d1520;

		--text-primary: #f0f4f8;
		--text-secondary: #94a3b8;
		--text-muted: #64748b;
		--text-accent: #38bdf8;

		--accent-primary: #3b82f6;
		--accent-secondary: #8b5cf6;
		--accent-green: #10b981;
		--accent-orange: #f59e0b;
		--accent-red: #ef4444;
		--accent-cyan: #06b6d4;

		--gradient-primary: linear-gradient(135deg, #3b82f6, #8b5cf6);
		--gradient-green: linear-gradient(135deg, #10b981, #06b6d4);
		--gradient-warm: linear-gradient(135deg, #f59e0b, #ef4444);
		--gradient-surface: linear-gradient(135deg, rgba(59, 130, 246, 0.08), rgba(139, 92, 246, 0.06));

		--border-color: rgba(148, 163, 184, 0.1);
		--border-active: rgba(59, 130, 246, 0.4);

		--radius-sm: 0.5rem;
		--radius-md: 0.75rem;
		--radius-lg: 1rem;
		--radius-xl: 1.25rem;
		--radius-full: 9999px;

		--shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.3);
		--shadow-md: 0 4px 12px rgba(0, 0, 0, 0.3);
		--shadow-lg: 0 8px 32px rgba(0, 0, 0, 0.4);
		--shadow-glow: 0 0 20px rgba(59, 130, 246, 0.15);

		--font-sans: 'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
		--font-display: 'Space Grotesk', var(--font-sans);

		--max-width: 1200px;
		--nav-height: 3.8rem;
	}

	/* ─── Global resets ─── */
	:global(html) {
		scroll-behavior: smooth;
		-webkit-text-size-adjust: 100%;
		overflow-x: hidden;
	}

	:global(body) {
		margin: 0;
		font-family: var(--font-sans);
		background: var(--bg-primary);
		color: var(--text-primary);
		min-height: 100vh;
		line-height: 1.6;
		-webkit-font-smoothing: antialiased;
		-moz-osx-font-smoothing: grayscale;
		overflow-x: hidden;
		width: 100%;
	}

	:global(*) {
		box-sizing: border-box;
	}

	:global(a) {
		color: inherit;
		text-decoration: none;
	}

	:global(section) {
		scroll-margin-top: calc(var(--nav-height) + 1.5rem);
	}

	:global(::selection) {
		background: rgba(59, 130, 246, 0.3);
		color: #fff;
	}

	:global(select) {
		max-width: 100%;
	}

	:global(img, svg, video) {
		max-width: 100%;
		height: auto;
	}

	/* ─── App shell ─── */
	.app {
		display: flex;
		flex-direction: column;
		min-height: 100vh;
	}

	main {
		flex: 1;
	}

	/* ─── Navbar ─── */
	.navbar {
		position: sticky;
		top: 0;
		z-index: 100;
		background: rgba(10, 15, 26, 0.85);
		backdrop-filter: blur(16px) saturate(1.5);
		-webkit-backdrop-filter: blur(16px) saturate(1.5);
		border-bottom: 1px solid var(--border-color);
		height: var(--nav-height);
	}

	.navbar-inner {
		max-width: var(--max-width);
		margin: 0 auto;
		padding: 0 1rem;
		height: 100%;
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.logo {
		display: flex;
		align-items: center;
		gap: 0.7rem;
		flex-shrink: 0;
	}

	.logo-icon {
		display: grid;
		place-items: center;
		width: 2.2rem;
		height: 2.2rem;
		border-radius: var(--radius-md);
		background: var(--gradient-primary);
		color: #fff;
	}

	.logo-text strong {
		display: block;
		font-family: var(--font-display);
		font-size: 1.05rem;
		font-weight: 700;
		color: var(--text-primary);
		line-height: 1.2;
	}

	.logo-text small {
		font-size: 0.72rem;
		color: var(--text-muted);
		font-weight: 500;
	}

	.nav-links {
		display: flex;
		gap: 0.25rem;
	}

	.nav-links a {
		padding: 0.45rem 0.9rem;
		border-radius: var(--radius-full);
		font-size: 0.88rem;
		font-weight: 600;
		color: var(--text-secondary);
		transition: all 0.2s ease;
	}

	.nav-links a:hover {
		color: var(--text-primary);
		background: rgba(59, 130, 246, 0.1);
	}

	/* Hamburger */
	.hamburger {
		display: none;
		flex-direction: column;
		gap: 4px;
		background: none;
		border: none;
		cursor: pointer;
		padding: 0.5rem;
		z-index: 110;
	}

	.hamburger span {
		display: block;
		width: 22px;
		height: 2px;
		background: var(--text-secondary);
		border-radius: 2px;
		transition: all 0.3s ease;
	}

	.hamburger.active span:nth-child(1) {
		transform: translateY(6px) rotate(45deg);
	}

	.hamburger.active span:nth-child(2) {
		opacity: 0;
	}

	.hamburger.active span:nth-child(3) {
		transform: translateY(-6px) rotate(-45deg);
	}

	.overlay {
		position: fixed;
		inset: 0;
		z-index: 90;
		background: rgba(0, 0, 0, 0.5);
	}

	/* ─── Footer ─── */
	.site-footer {
		padding: 2rem 1.25rem;
		text-align: center;
		border-top: 1px solid var(--border-color);
		color: var(--text-muted);
		font-size: 0.82rem;
	}

	.site-footer p {
		margin: 0;
		max-width: var(--max-width);
		margin: 0 auto;
	}

	/* ─── Mobile ─── */
	@media (max-width: 768px) {
		.hamburger {
			display: flex;
		}

		.logo-text small {
			display: none;
		}

		.logo-icon {
			width: 1.9rem;
			height: 1.9rem;
		}

		.logo-icon svg {
			width: 16px;
			height: 16px;
		}

		.logo-text strong {
			font-size: 0.95rem;
		}

		.navbar-inner {
			padding: 0 0.75rem;
		}

		.nav-links {
			position: fixed;
			top: 0;
			right: -100%;
			width: 70%;
			max-width: 280px;
			height: 100vh;
			height: 100dvh;
			background: var(--bg-secondary);
			border-left: 1px solid var(--border-color);
			flex-direction: column;
			padding: 5rem 1.5rem 2rem;
			gap: 0.5rem;
			z-index: 95;
			transition: right 0.3s ease;
		}

		.nav-links.open {
			right: 0;
		}

		.nav-links a {
			padding: 0.8rem 1rem;
			border-radius: var(--radius-md);
			font-size: 1rem;
		}

		.nav-links a:hover {
			background: rgba(59, 130, 246, 0.15);
		}
	}

	@media (max-width: 380px) {
		.logo {
			gap: 0.4rem;
		}

		.logo-text strong {
			font-size: 0.88rem;
		}
	}
</style>

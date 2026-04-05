# IPL Cricket Model Lab

Local-first IPL winner prediction and fantasy XI dashboard built with SvelteKit.

## What it uses

- Historical base: Kaggle dataset `chaitu20/ipl-dataset2008-2025`
- Current context: official IPL 2026 squad pages
- Local storage:
  - `data/local/ipl.duckdb`
  - `data/generated/app-data.json`

## Setup

1. Install Node dependencies:

```bash
npm install
```

2. Install Python dependencies:

```bash
python -m pip install -r requirements.txt
```

3. Build the local dataset:

```bash
npm run data:build
```

4. Start the app:

```bash
npm run dev
```

## Useful commands

```bash
npm run check
npm run build
npm run preview
```

## Notes

- Historical modeling is based on Kaggle data through 2025.
- 2026 squad context is scraped from official IPL team pages.
- Fantasy credits are local proxy credits, not official Dream11 credits.

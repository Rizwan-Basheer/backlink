# Backlink Creator Bot

The Backlink Creator Bot is a modular automation framework for building backlinks across different websites. It follows a **train once, run many** approach and now includes a FastAPI powered admin panel for complete recipe lifecycle management.

## Features

### Bot Engine
- **Trainer** – record browser actions and save YAML recipes.
- **Recipe Manager** – validates and stores recipes with automatic versioning snapshots.
- **Executor** – runs recipes using Playwright with variable substitution and execution tracking.
- **Variables Manager** – loads CSV datasets and rotates accounts/content for placeholders like `{{accounts.username}}`.
- **Structured Logging** – rotating log files plus optional screenshots per execution.

### Admin Panel
- Overview dashboard with key metrics, category breakdown and notifications.
- Recipe management: search, filter, pause/resume, archive and run recipes individually or in bulk.
- Execution visibility: status, logs, error messages and timestamps.
- Category management: create categories and triage user category requests.
- Import/export of all categories, recipes, versions and execution history.

### CLI
Built with [Typer](https://typer.tiangolo.com/) and [Rich](https://rich.readthedocs.io/), the CLI lets you:

- Initialise the database: `backlink-bot init`
- Manage recipes: train, list, run, schedule and pause/resume.
- Manage categories and category requests.
- Inspect executions and export/import bot state.
- Launch the admin panel server: `backlink-bot serve-admin`

## Project Layout

```
backlink_bot/
├── admin/                # FastAPI admin application & templates
├── bot/                  # Automation modules (actions, executor, trainer, variables)
├── utils/                # Logging utilities
├── config.py             # Central configuration and directories
├── cli.py                # Typer CLI entry point
├── db.py                 # SQLModel models and engine
├── services.py           # Service layer shared by CLI and admin
└── ...
```

Recipe YAML files are stored in `data/recipes/` with version snapshots under `data/versions/`. Executions write logs to `data/logs/` and screenshots to `data/screenshots/`.

## Getting Started

1. **Install dependencies** (use the browser extra to include Playwright):

   ```bash
   pip install -e .[browser]
   playwright install
   ```

2. **Initialise the database**:

   ```bash
   backlink-bot init
   ```

3. **Create at least one category**:

   ```bash
   backlink-bot categories create "Profile Backlinks" --description "Profile creation backlinks"
   ```

4. **Train a recipe** (interactive prompts):

   ```bash
   backlink-bot recipes train
   ```

5. **Run the admin panel**:

   ```bash
   backlink-bot serve-admin --host 0.0.0.0 --port 8000
   ```

   Open `http://localhost:8000` to monitor recipes, executions and requests.

## Configuration

Environment variables can override storage locations:

- `BACKLINK_HOME` – base directory
- `BACKLINK_RECIPES_DIR`, `BACKLINK_LOG_DIR`, `BACKLINK_CSV_DIR`, `BACKLINK_VERSION_DIR`, `BACKLINK_SCREENSHOT_DIR`
- `BACKLINK_DB` – SQLite database path
- `BACKLINK_HEADLESS` – set to `false` to run Playwright in headed mode

## Data Files

CSV datasets can be placed in `data/csv/` and referenced in recipes by filename. The variables manager rotates records automatically when executing recipes to provide different accounts or content.

## Testing & Development

- The project relies on Playwright's async API. Install browsers via `playwright install`.
- Admin panel uses SQLModel with SQLite; data lives in `data/backlink.db`.
- Logs are written to `data/logs/` with rotation and can be inspected for troubleshooting.

## Roadmap

- Proxy/IP rotation support
- Advanced analytics (time series charts)
- Distributed execution queues

## License

MIT

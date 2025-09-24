# Backlink Creator Bot

The Backlink Creator Bot is a modular automation framework for recording and executing backlink building workflows. Recipes are stored as YAML files and can be replayed automatically with variable substitution for accounts, content and runtime data. A FastAPI-powered admin panel provides visibility into recipes, executions and category management.

## Features

- **Recipe training and management**: build reusable automation recipes with version history stored as YAML files.
- **Execution engine**: run recipes headless or in dry-run mode with structured logging.
- **Variable rotation**: load CSV data sources and substitute `{{placeholders}}` in actions.
- **Scheduling primitives**: prepare recurring recipe or category runs.
- **Admin panel**: monitor analytics, trigger reruns, manage categories and handle category requests.
- **Target-aware execution**: register target URLs, auto-enrich metadata and queue recipe runs per target.
- **AI-assisted content**: generate bios, captions or blog posts that reference each target while respecting recipe-specific hints.
- **Self-healing automation**: automatically request LLM selector suggestions when Playwright steps fail (with bounded retries).
- **CLI tooling**: Typer-based CLI for listing recipes, running executions, planning dry-runs and managing targets.

## Project structure

```
├── data/                  # Runtime artefacts (created automatically)
├── src/backlink/
│   ├── actions/           # Playwright integration
│   ├── admin/             # FastAPI admin panel
│   ├── cli/               # Typer CLI entry point
│   ├── services/          # Domain services (recipes, variables, analytics, etc.)
│   ├── utils/             # Shared helpers
│   └── models.py          # SQLModel entities
└── pyproject.toml         # Project metadata and dependencies
```

## Getting started

1. Install dependencies (preferably inside a virtual environment):

   ```bash
   pip install -e .
   ```

   > **Note:** The project pins `bcrypt<4` to remain compatible with the version of
   > `passlib` that provides the default password hashing backends, which is
   > especially important on Windows where incompatible wheels can fail to
   > install. A regular `pip install -e .` will resolve the correct version
   > automatically.

   Optionally install Playwright support:

   ```bash
   pip install -e .[playwright]
   playwright install chromium
   ```

2. Initialise the SQLite database:

   ```bash
   backlink init-db
   ```

3. Start the admin panel:

   ```bash
   uvicorn backlink.admin.app:app --reload
   ```

4. Use the CLI to import or list recipes:

   ```bash
   backlink recipes list
   ```

## Data directories

Runtime artefacts such as recipe YAML files, execution logs and CSV data are stored inside the `data/` directory. The folder hierarchy is created on first import of `backlink.config`.

## Recipe format

Recipes are stored as YAML files with the following structure:

```yaml
metadata:
  name: Example profile backlink
  site: example.com
  description: Creates a profile and updates the bio link.
  category_id: 1
  status: ready
variables:
  LOGIN_USER: "{{env.WP_USER}}"
  LOGIN_PASS: "{{env.WP_PASS}}"
content_requirements:
  profile_backlinks:
    tone: friendly
    min_bio_words: 60
    min_caption_words: 20
actions:
  - name: open homepage
    action: goto
    value: https://example.com
  - name: fill username
    action: fill
    selector: input[name="username"]
    value: "{{LOGIN_USER}}"
  - name: fill bio
    action: fill
    selector: textarea#bio
    value: "{{GENERATED_BIO}}"
config:
  headless: true
  per_action_delay_ms: 500
```

## Targets & runtime content

The executor now requires an explicit target URL for each run. Targets can be registered and enriched from the CLI:

```bash
backlink targets add https://example.com/post/how-to-paint
backlink targets enrich 1
```

Profile and blog recipes automatically request tailored AI content, expose generated placeholders such as `{{GENERATED_BIO}}` and `{{GENERATED_BLOG}}`, and reuse cached results unless `--refresh-content` is passed.

Use the run helpers to execute recipes for specific targets or queue multiple targets:

```bash
backlink recipes run --recipe 3 --target 1 --headless False
backlink run-target --target 1 --category "Profile Backlinks"
backlink run-queue --category "Blog Backlinks" --limit 5
backlink recipes plan --recipe 3 --target 1
```

## Testing

The project uses SQLite for persistence. Automated browser execution is stubbed when Playwright is not installed, enabling development and testing without browser dependencies.

## License

MIT

# Cribl Pusher

Automates adding **routes** and upserting **destinations** (blob storage outputs) across Cribl workspaces. Supports single-app and bulk-file modes with a full diff preview, safety guards, and automatic rollback snapshots before every write.

Also includes **`rode_rm.py`** — a companion script that pushes **ELK roles + role-mappings** and **Cribl routes/destinations** together in a single run, with configurable ordering and per-side skip flags.

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Prerequisites](#prerequisites)
3. [File Structure](#file-structure)
4. [First-Time Setup](#first-time-setup)
5. [Configuration Reference](#configuration-reference)
6. [Template Files](#template-files)
7. [App Input Format](#app-input-format)
8. [Running the Script](#running-the-script)
9. [rode_rm.py — ELK Roles + Cribl](#rode_rmpy--elk-roles--cribl)
10. [Web UI](#web-ui)
11. [Docker](#docker)
12. [Serving via Apache httpd (bastion)](#serving-via-apache-httpd-bastion)
13. [All CLI Flags](#all-cli-flags)
14. [Logging](#logging)
15. [Safety Features](#safety-features)
16. [Rolling Back a Change](#rolling-back-a-change)
17. [Troubleshooting](#troubleshooting)

---

## What It Does

For each application you provide (by ID and name), the script:

1. Fetches the current route table from Cribl (`GET /api/v1/m/{worker_group}/routes/{routes_table}`)
2. Fetches all existing destinations (`GET /system/outputs`) to build a skip-list
3. Inserts a new route above the catch-all/default route — skipping any that already exist
4. Shows a full unified diff so you can review exactly what will change
5. Asks for confirmation before writing anything
6. Saves a rollback snapshot of the original route table
7. Creates any destination that does not already exist (`POST /system/outputs`) — skips if present
8. Patches the route table back to Cribl (`PATCH /api/v1/m/{worker_group}/routes/{routes_table}`)

Each workspace can point to a **different Cribl cluster** via an optional per-workspace `base_url`, or you can override the URL at runtime with `--cribl-url`.

---

## Prerequisites

- **Python 3.10 or newer** *(not needed if running via Docker)*
- **Docker Desktop** *(optional — for the containerised option)*
- **pip** packages:

```bash
# CLI only
pip install requests urllib3 jinja2

# CLI + Flask web UI (recommended)
pip install requests urllib3 jinja2 flask

# CLI + Streamlit web UI (alternative)
pip install requests urllib3 jinja2 streamlit
```

Verify your Python version:

```bash
python --version
# Should print Python 3.10.x or higher
```

---

## File Structure

```
cribl-flask/
│
├── cribl-pusher.py              # CLI entry point — add routes + upsert destinations
├── rode_rm.py                   # Companion CLI — pushes ELK roles + Cribl routes together
├── app.py                       # Flask web UI — run with: python app.py
├── ui.py                        # Streamlit web UI (alternative) — run with: streamlit run ui.py
├── cribl_api.py                 # Cribl API + route logic
├── cribl_config.py              # Config loading and workspace resolution
├── cribl_utils.py               # Shared utilities (I/O, prompts, HTTP session)
├── cribl_logger.py              # Logging setup
├── _validate.py                 # Offline validation script — run with: python _validate.py
│
├── Dockerfile                   # Container image definition
├── requirements.txt             # Pip dependencies
│
├── config.json                  # YOUR config (credentials + workspaces) — never commit
├── config.example.json          # Safe-to-commit template — copy this to config.json
│
├── route_template_azn.json      # Route shape for Azure North  ← you must create
├── route_template_azs.json      # Route shape for Azure South  ← you must create
├── blob_dest_template_azn_dev.json   # Dest shape — AZN dev    ← you must create
├── blob_dest_template_azs_dev.json   # Dest shape — AZS dev    ← you must create
├── blob_dest_template_azn_prod.json  # Dest shape — AZN prod   ← you must create
├── blob_dest_template_azs_prod.json  # Dest shape — AZS prod   ← you must create
│
├── appids.txt                   # (optional) Bulk app list — one "appid,appname" per line
│
├── ops_rm_r_templates_output/   # Auto-created by rode_rm.py — ELK template files saved here
│
└── cribl_snapshots/             # Auto-created — rollback snapshots saved here
    ├── dev/
    ├── test/
    └── prod/
```

> `config.json` and `cribl_snapshots/` are in `.gitignore` and will never be committed.

---

## First-Time Setup

### Step 1 — Clone / copy the files

Make sure all `.py` files, template `.json` files, and `config.example.json` are in the same folder.

### Step 2 — Install dependencies

```bash
pip install requests urllib3 jinja2 flask
```

Or install everything from the requirements file:

```bash
pip install -r requirements.txt
```

### Step 3 — Create your config file

```bash
# Windows
copy config.example.json config.json

# Mac / Linux
cp config.example.json config.json
```

### Step 4 — Edit config.json

Open `config.json` in any text editor and fill in:

| Field | What to put |
|---|---|
| `base_url` | Default Cribl URL, e.g. `https://cribl.company.com:9000` |
| `cribl_urls` | List of Cribl URLs shown as a dropdown in the UI |
| `elk_urls` | List of ELK URLs shown as a dropdown in the UI |
| `credentials.username` | Your Cribl login username |
| `credentials.password` | Your Cribl login password (or leave blank to type it at runtime) |
| `credentials.token` | A pre-generated bearer token — if set, username/password are ignored |
| `workspaces` | One entry per worker group you want to target (see below) |

**Example with multiple clusters:**

```json
{
  "base_url": "https://cribl-azn.company.com:9000",
  "cribl_urls": [
    "https://cribl-azn.company.com:9000",
    "https://cribl-azs.company.com:9000"
  ],
  "elk_urls": [
    "https://elk-azn.company.com:9200",
    "https://elk-azs.company.com:9200"
  ],
  "skip_ssl": false,
  "credentials": {
    "token": "",
    "username": "admin",
    "password": "yourpassword"
  },
  "route_templates": {
    "azn": "route_template_azn.json",
    "azs": "route_template_azs.json"
  },
  "dest_prefixes": {
    "azn": "hcsc-blob-storage-northcentralus",
    "azs": "hcsc-blob-storage-southcentralus"
  },
  "snapshot_dir": "cribl_snapshots",
  "min_existing_total_routes": 1,
  "diff_lines": 3,
  "workspaces": {
    "dev": {
      "worker_groups": ["wg-dev-01", "wg-dev-02"],
      "dest_templates": {
        "azn": "blob_dest_template_azn_dev.json",
        "azs": "blob_dest_template_azs_dev.json"
      },
      "description": "Development"
    },
    "prod": {
      "worker_groups": ["wg-prod-01", "wg-prod-02"],
      "dest_templates": {
        "azn": "blob_dest_template_azn_prod.json",
        "azs": "blob_dest_template_azs_prod.json"
      },
      "description": "Production",
      "require_allow": true
    }
  }
}
```

### Step 5 — Create the template files

The following files must exist in the same folder. Grab the shapes from your live Cribl instance:

**`route_template_azn.json` / `route_template_azs.json`** — fetch a route from Cribl and strip out the app-specific fields:

```bash
curl -k -H "Authorization: Bearer YOUR_TOKEN" \
  "https://YOUR_CRIBL:9000/api/v1/m/{worker_group}/routes/{routes_table}"
```

Minimum working example:

```json
{
  "pipeline": "passthru",
  "final": false,
  "disabled": false,
  "clones": [],
  "description": "",
  "enableOutputExpression": false
}
```

**`blob_dest_template_{region}_{workspace}.json`** — fetch an existing output and strip the app-specific fields:

```bash
curl -k -H "Authorization: Bearer YOUR_TOKEN" \
  "https://YOUR_CRIBL:9000/api/v1/m/{worker_group}/system/outputs/{output_id}"
```

The script fills in `id`, `name`, `containerName`, and `description` automatically.

### Step 6 — Do a dry run

```bash
python cribl-pusher.py --workspace dev --worker-group wg-dev-01 --region azn --dry-run --appid TEST001 --appname "Test App"
```

You should see the `=== TARGET ===` banner and a diff preview with no errors. **Nothing is written on a dry run.**

---

## Configuration Reference

### Top-level fields

| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | string | — | Default Cribl root URL (overridden per workspace or via `--cribl-url`) |
| `cribl_urls` | list | `[]` | Cribl URLs shown as a dropdown in the UI Cribl Pusher tab |
| `elk_urls` | list | `[]` | ELK URLs shown as a dropdown in the UI ELK Roles + Cribl tab |
| `skip_ssl` | bool | `false` | Disable SSL cert verification globally |
| `credentials.token` | string | `""` | Bearer token — if set, skips username/password login |
| `credentials.username` | string | `""` | Login username |
| `credentials.password` | string | `""` | Login password |
| `route_templates` | object | — | Map of region → route template path: `{"azn": "route_template_azn.json", "azs": "..."}` |
| `dest_prefixes` | object | — | Map of region → destination ID prefix: `{"azn": "hcsc-blob-storage-northcentralus", ...}` |
| `snapshot_dir` | string | `cribl_snapshots` | Directory where rollback snapshots are saved |
| `min_existing_total_routes` | int | `1` | Refuse to PATCH if fewer than this many routes are loaded |
| `diff_lines` | int | `3` | Lines of context shown in the diff preview |

### Workspace fields

Each key under `workspaces` is a name you choose (e.g. `"dev"`, `"prod"`).

| Field | Required | Description |
|---|---|---|
| `worker_groups` | yes | List of Cribl worker group names available for selection (e.g. `["wg-dev-01", "wg-dev-02"]`) |
| `dest_templates` | yes* | Object mapping region → dest template path: `{"azn": "blob_dest_template_azn_dev.json", "azs": "..."}` |
| `dest_template` | yes* | Alternative: single dest template path — skips region lookup (use when region doesn't matter) |
| `base_url` | no | Overrides the global `base_url` — use this to point a workspace at a different cluster |
| `routes_table` | no | Route table name in `GET/PATCH /routes/{routes_table}`. Defaults to `"default"` |
| `description` | no | Human-readable label shown in the run banner and UI dropdown |
| `require_allow` | no | If `true`, user must type `ALLOW` before any writes (recommended for prod) |
| `skip_ssl` | no | Overrides the global `skip_ssl` for this workspace only |
| `route_template` | no | Per-workspace route template override — skips region lookup in `route_templates` map |

*One of `dest_templates` or `dest_template` is required.

### Credential priority (highest to lowest)

```
1. --token / --username / --password  CLI flags
2. CRIBL_TOKEN / CRIBL_USERNAME / CRIBL_PASSWORD  environment variables
3. credentials block in config.json
```

---

## Template Files

### route_template_azn.json / route_template_azs.json

One file per region, referenced via `route_templates` in `config.json`. The script fills in `id`, `filter`, `output`, and `name` for each app automatically. All other fields come from this template.

### blob_dest_template_{region}_{workspace}.json

One file per region × workspace (e.g. `blob_dest_template_azn_dev.json`), referenced via `dest_templates` in each workspace config. The script fills in `id`, `name`, `containerName`, and `description` automatically.

---

## App Input Format

### Single app — via CLI flags

```bash
python cribl-pusher.py --appid APP001 --appname "My Application"
```

### Bulk apps — via text file

Create a file (default name: `appids.txt`) with one app per line:

```
# Lines starting with # are comments and are ignored
APP001, My First Application
APP002, My Second Application
APP003, Another App
```

Rules:
- Format is `appid, appname` (comma-separated)
- Leading/trailing spaces are trimmed
- Blank lines and `#` comments are skipped
- Both fields are required

---

## Running the Script

### Option A — Web UI (recommended)

**Flask UI** (default, no extra dependencies beyond `flask`):

```bash
python app.py
```

Opens `http://localhost:5000`.

**Streamlit UI** (alternative, requires `pip install streamlit`):

```bash
streamlit run ui.py
```

Opens `http://localhost:8501`. See the [Web UI](#web-ui) section for details.

---

### Option B — CLI (single app)

```bash
python cribl-pusher.py \
  --workspace dev \
  --worker-group wg-dev-01 \
  --region azn \
  --appid APP001 \
  --appname "My Application" \
  --yes
```

---

### Option C — CLI (bulk file)

```bash
python cribl-pusher.py \
  --workspace dev \
  --worker-group wg-dev-01 \
  --region azn \
  --from-file \
  --appfile appids.txt \
  --yes
```

---

### Dry run (preview only — no writes)

```bash
python cribl-pusher.py --workspace dev --worker-group wg-dev-01 --region azn --dry-run --from-file --appfile appids.txt
```

---

### Override the Cribl URL at runtime

```bash
python cribl-pusher.py \
  --cribl-url https://cribl-azs.company.com:9000 \
  --workspace dev \
  --worker-group wg-dev-01 \
  --region azs \
  --appid APP001 --appname "My App" \
  --yes
```

---

### Production workspace

Workspaces with `"require_allow": true` require an extra flag:

```bash
python cribl-pusher.py \
  --workspace prod \
  --worker-group wg-prod-01 \
  --region azn \
  --allow-prod \
  --from-file --appfile appids.txt \
  --yes
```

---

### Using a route group

```bash
python cribl-pusher.py \
  --workspace dev \
  --worker-group wg-dev-01 \
  --region azn \
  --group-id my-group-id \
  --create-missing-group \
  --group-name "My New Group" \
  --from-file
```

---

## rode_rm.py — ELK Roles + Cribl

`rode_rm.py` applies **ELK roles/role-mappings** and **Cribl routes/destinations** in a single command. Both sides can run together or independently.

### What it does

1. Generates ELK role and role-mapping templates (always saved to `ops_rm_r_templates_output/`)
2. (ELK side) Pushes roles and role-mappings to Elasticsearch via `PUT /_security/role/{name}` and `PUT /_security/role_mapping/{name}`
3. (Cribl side) Runs the same route + destination upsert logic as `cribl-pusher.py`
4. Runs the two sides in the configured order (`elk-first` by default)

### Generated ELK templates

Every run saves four files to `ops_rm_r_templates_output/`:

| File | Description |
|---|---|
| `roles_{apmid}.json` | Kibana Dev Console format (for human review) |
| `role_mappings_{apmid}.json` | Kibana Dev Console format (for human review) |
| `roles_{apmid}_pushable.json` | JSON array with `method`/`path`/`body` — ready to push via API |
| `role_mappings_{apmid}_pushable.json` | JSON array with `method`/`path`/`body` — ready to push via API |

### Basic usage

```bash
python rode_rm.py \
  --app_name "My Application" \
  --apmid    "app00001234" \
  --elk-url  "https://elk.company.com:9200" \
  --elk-user elastic \
  --elk-password secret \
  --workspace dev \
  --dry-run
```

### Generate templates only (no API calls)

```bash
python rode_rm.py \
  --app_name "My Application" \
  --apmid    "app00001234" \
  --skip-elk \
  --skip-cribl
```

### Override the Cribl URL

```bash
python rode_rm.py \
  --app_name "My App" --apmid "app00001234" \
  --elk-url "https://elk.company.com:9200" --elk-user elastic \
  --cribl-url "https://cribl-azs.company.com:9000" \
  --workspace dev
```

### CLI flags

| Flag | Default | Description |
|---|---|---|
| `--app_name` | *(required)* | Application name |
| `--apmid` | *(required)* | App ID (lower-case, e.g. `app00001234`) |
| `--from-file` | false | Read app list from file instead of `--app_name`/`--apmid` |
| `--appfile` | `appids.txt` | Path to app list file (one `appid, appname` per line) |
| `--elk-url` | *(required unless --skip-elk)* | ELK/OpenSearch nonprod base URL |
| `--elk-url-prod` | *(required unless --skip-elk)* | ELK/OpenSearch prod base URL |
| `--elk-user` | `""` | ELK nonprod username (basic auth) |
| `--elk-password` | `""` | ELK nonprod password |
| `--elk-token` | `""` | ELK nonprod API key — overrides user/password |
| `--elk-user-prod` | `""` | ELK prod username (basic auth) |
| `--elk-password-prod` | `""` | ELK prod password |
| `--elk-token-prod` | `""` | ELK prod API key — overrides user/password |
| `--cribl-url` | `""` | Cribl base URL override |
| `--workspace` | *(required unless --skip-cribl)* | Cribl workspace name |
| `--allow-prod` | false | Skip the ALLOW prompt for protected workspaces |
| `--order` | `elk-first` | Execution order: `elk-first` or `cribl-first` |
| `--skip-elk` | false | Skip the ELK side (templates are still saved) |
| `--skip-cribl` | false | Skip the Cribl side |
| `--dry-run` | false | Preview only — no writes on either side |
| `--skip-ssl` | false | Disable SSL verification for all connections |
| `--log-level` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--yes` | false | Skip the confirmation prompt |

---

## Web UI

Two web UI options are available:

### Flask UI (app.py) — recommended

```bash
python app.py
```

Opens `http://localhost:5000`. The Flask UI exposes two API endpoints:

- **`POST /cribl/api/run-pusher`** — runs `cribl-pusher.py` for one or more worker groups
- **`POST /cribl/api/run-remove`** — runs `rode_rm.py` (ELK + Cribl)
- **`GET /cribl/app`** — main app page (served from `templates/app.html`)
- **`GET /`** — landing page (served from `templates/index.html`)
- **`GET /health`** — health check endpoint

### Streamlit UI (ui.py) — alternative

```bash
streamlit run ui.py
```

Opens `http://localhost:8501`. The Streamlit UI has two tabs:

### Tab 1 — Cribl Pusher

- **Cribl URL** — select from the `cribl_urls` list in config (or type a custom URL if the list is empty)
- **Workspace** — select from workspaces defined in config
- **App Input** — single app (App ID + App Name) or bulk file upload
- **Options** — Dry Run (default: on), Skip SSL, Log Level
- **Credentials override** — Bearer Token or Username/Password (leave blank to use config.json)
- **Advanced Options** — Route Group ID, safety overrides, snapshot directory, log file

### Tab 2 — ELK Roles + Cribl

- **App ID + App Name** — used for both ELK role names and Cribl route/destination
- **ELK URL** — select from the `elk_urls` list in config (or type a custom URL)
- **ELK credentials** — API token or username/password
- **Cribl URL** — select from the `cribl_urls` list in config (or type a custom URL)
- **Workspace** — select from workspaces defined in config
- **Options** — Dry Run (default: on), Skip SSL, Log Level, Order (ELK first / Cribl first)
- **Skip sides** — Skip ELK or Skip Cribl independently

> **Dry Run defaults to ON** in both tabs. Uncheck it to perform actual writes.

Sensitive fields (passwords, tokens) are masked in the command preview shown before each run.

---

## Docker

The image is built on `python:3.13-slim` (linux/amd64). `config.json` and all template JSONs are **never baked in** — they are volume-mounted at runtime.

### Build

```bash
docker build -t cribl-pusher .
```

### Run — local development

```bash
# Linux / macOS / Git Bash
docker run -d --name cribl-pusher \
  -p 5000:5000 \
  -v $(pwd)/config.json:/app/config.json:ro \
  cribl-pusher
```

Then open `http://localhost:5000`.

### Run — production (behind Apache on bastion)

Bind to loopback only so the port is not exposed to the public internet.

```bash
docker run -d --name cribl-pusher --restart unless-stopped \
  -p 10.0.0.2:5000:5000 \
  -v /path/to/config.json:/app/config.json:ro \
  -v /path/to/cribl_snapshots:/app/cribl_snapshots \
  cribl-pusher
```

> **`10.0.0.2`** is the WireGuard interface IP on the remote host. Binding to it means
> the container is reachable from the bastion over VPN but not from the public internet.

---

## Serving via Apache httpd (bastion)

The app runs in Docker on a **remote host**. Apache on the **bastion** reverse-proxies to it over a WireGuard VPN or SSH tunnel.

```
Browser → https://bastion/cribl/app
          Apache ProxyPass → http://10.0.0.2:5000/cribl/app  (WireGuard)
          Docker container → Flask :5000
```

### Static landing page

`index.html` is a static info/documentation page served directly by Apache at `/cribl/`.

```bash
sudo mkdir -p /var/www/html/cribl
sudo cp index.html /var/www/html/cribl/
```

### Apache config (add to your existing VirtualHost)

Add the contents of `httpd-add-to-existing.conf` inside your existing `<VirtualHost>` block:

```bash
sudo vi /etc/httpd/conf.d/your-existing.conf
# paste contents of httpd-add-to-existing.conf inside <VirtualHost>

sudo httpd -t && sudo systemctl reload httpd
```

| URL | What |
|---|---|
| `https://bastion/cribl/` | Static landing page |
| `https://bastion/cribl/app` | Live Flask app |

### Required Apache modules

```bash
httpd -M | grep -E 'proxy|rewrite'
# proxy_module, proxy_http_module, proxy_wstunnel_module, rewrite_module must be listed
```

---

### Save, Split, and Transfer

```bash
# Export and split into 25 MB chunks
docker save cribl-pusher:latest -o cribl-pusher.tar
split -b 25m cribl-pusher.tar cribl-pusher.part.
sha256sum cribl-pusher.tar > cribl-pusher.tar.sha256
```

Transfer all `cribl-pusher.part.*` files to the target machine, then:

```bash
cat cribl-pusher.part.* > cribl-pusher.tar
sha256sum -c cribl-pusher.tar.sha256
docker load -i cribl-pusher.tar
```

---

## All CLI Flags

### cribl-pusher.py

| Flag | Default | Description |
|---|---|---|
| `--config` | `config.json` | Path to the config file |
| `--cribl-url` | `""` | Cribl base URL override (overrides config + workspace `base_url`) |
| `--workspace` | *(prompts)* | Workspace name (must match a key in config `workspaces`) |
| `--worker-group` | *(prompts)* | Worker group to target (must be in workspace's `worker_groups` list) |
| `--region` | *(prompts)* | Region: `azn` or `azs` (selects route + dest templates) |
| `--allow-prod` | false | Skip the ALLOW prompt for workspaces with `require_allow: true` |
| `--token` | `""` | Bearer token override |
| `--username` | `""` | Username override |
| `--password` | `""` | Password override |
| `--skip-ssl` | false | Disable SSL verification |
| `--dry-run` | false | Preview only — no API writes |
| `--yes` | false | Skip the final `YES` confirmation prompt |
| `--appid` | *(prompts)* | Single app ID |
| `--appname` | *(prompts)* | Single app name (required with `--appid`) |
| `--from-file` | false | Load apps from a file |
| `--appfile` | `appids.txt` | Path to the apps file |
| `--group-id` | `""` | Insert routes into this route-group ID |
| `--create-missing-group` | false | Create the group if it doesn't exist |
| `--group-name` | `""` | Display name when creating a missing group |
| `--min-existing-total-routes` | *(from config)* | Override the safety minimum route count |
| `--diff-lines` | *(from config)* | Lines of context in the diff preview |
| `--snapshot-dir` | *(from config)* | Override the snapshot directory |
| `--log-level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--log-file` | `""` | Append logs to this file in addition to the console |

---

## Logging

All output uses Python's `logging` module via the shared `"cribl"` logger.

### Log levels

| Level | What you see |
|---|---|
| `ERROR` | Only errors and fatal messages |
| `WARNING` | Errors + warnings |
| `INFO` | Normal run output — targets, plan, OK/SKIP/SNAPSHOT lines *(default)* |
| `DEBUG` | Everything above + each HTTP verb/URL + per-route detail |

```bash
# Write logs to a file (appended across runs)
python cribl-pusher.py --workspace dev --worker-group wg-dev-01 --region azn --log-file audit.log --from-file --yes
```

---

## Safety Features

| Guard | What it does |
|---|---|
| **Diff preview** | Always shows a full unified diff before asking for confirmation |
| **Minimum routes check** | Refuses to PATCH if the API returns fewer routes than `min_existing_total_routes` |
| **No-shrink check** | Refuses to PATCH if the new total route count is less than the current count |
| **Duplicate skip** | Skips any app whose route name or filter already exists |
| **require_allow** | Protected workspaces require typing `ALLOW` or passing `--allow-prod` |
| **Dry run** | Runs the full logic (auth + GET) but never calls POST or PATCH |
| **Rollback snapshot** | Original route object saved to `cribl_snapshots/{workspace}/` before every PATCH |

---

## Rolling Back a Change

Find the snapshot file printed in the run output:

```
[SNAPSHOT] cribl_snapshots/prod/routes_snapshot_20240315T143022Z.json
```

Restore it using the `routes_url` from the `=== TARGET ===` banner:

```bash
curl -k -X PATCH \
  "https://YOUR_CRIBL:9000/api/v1/m/{worker_group}/routes/{routes_table}" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d @cribl_snapshots/prod/routes_snapshot_20240315T143022Z.json
```

---

## Troubleshooting

### `Config file not found: config.json`

```bash
copy config.example.json config.json   # Windows
cp config.example.json config.json     # Mac/Linux
```

---

### `FileNotFoundError: route_template_azn.json` (or similar)

The template files are not created automatically. See [Step 5 — Create the template files](#step-5--create-the-template-files).

---

### `[ERR] login failed: 401`

- Wrong username/password in `config.json`
- Or use a token: generate one in Cribl UI under **Settings → API tokens** and set `credentials.token`

---

### `SSL: CERTIFICATE_VERIFY_FAILED`

```json
"skip_ssl": true
```

Or pass `--skip-ssl` at runtime.

---

### `[SAFETY] Refusing to PATCH: total_before=0 < min=1`

The GET returned an empty route table. Check `base_url`, `worker_group`, and that your token has permission to read routes.

---

### `json.decoder.JSONDecodeError` when running rode_rm.py

The ELK template body failed to parse. This usually means the Jinja2 template rendered invalid JSON. Run with `--skip-elk --skip-cribl` first to generate and inspect the template files in `ops_rm_r_templates_output/`.

---

### `ModuleNotFoundError: No module named 'jinja2'`

```bash
pip install jinja2
```

---

### `ModuleNotFoundError: No module named 'requests'`

```bash
pip install requests urllib3
```

---

### Streamlit UI shows a blank right panel after clicking Run

The script likely exited with an error before producing output. Check:
- `config.json` has the correct `base_url` and credentials
- The workspace's `dest_template` file exists
- Enable **Debug** log level in the UI for detailed HTTP output

---

### Docker container can't reach Cribl

If Cribl is running on the same host machine, use `host.docker.internal` instead of `localhost`:

```json
"base_url": "https://host.docker.internal:9000"
```

# Cribl Pusher — Application Onboarding Guide

**Tool:** Cribl Pusher (Internal)
**Access:** https://bastion/cribl/app
**Repo:** *(link to your internal repo)*
**Owner / Contact:** *(your team name or Slack channel)*

---

## What is this tool?

Cribl Pusher is an internal web app that automates application onboarding into Cribl and ELK. Instead of manually editing route tables or running curl commands, you fill out a form and the tool handles authentication, diff preview, safety checks, and the API writes for you.

It supports two workflows:

| Tab | Use when… |
|---|---|
| **Tab 1 — Cribl Pusher** | You only need to add Cribl routes and blob storage destinations |
| **Tab 2 — ELK Roles + Cribl Routes** | You need to create ELK security roles/role-mappings AND Cribl routes in a single operation |

> **Dry Run is on by default.** No changes are written until you uncheck it. Always review the diff output first.

---

## Accessing the app

Navigate to the landing page:

```
https://bastion/cribl/
```

[SCREENSHOT 1 — Landing page showing Overview cards, Quick Start steps, and the "Launch App" button]

Click **Launch App** (top-right or hero button) to open the tool.

---

## Tab 1 — Cribl Pusher (step by step)

Use this tab when onboarding an app that only needs Cribl routes and destinations added. This is the most common workflow.

---

### Step 1 — Select a workspace and worker group(s)

Pick the **Workspace** from the dropdown (e.g. `dev`, `test`, `prod`). The worker group list below it will populate automatically — check one or more groups to target.

Set the **Region** (`azn` for Azure North, `azs` for Azure South).

[SCREENSHOT 2 — Tab 1 with a workspace selected, worker groups checkboxes visible, region radio buttons]

> If you select a **protected workspace** (e.g. `prod`), an amber warning banner appears. You must check **Allow production writes** before the Run button will do anything.

[SCREENSHOT 3 — Protected workspace amber banner with "Allow production writes" checkbox]

---

### Step 2 — Enter app details

Choose **Single App** or **Bulk File** mode.

**Single App** — type the App ID and App Name directly:

[SCREENSHOT 4 — Single App mode with App ID and App Name fields filled in]

**Bulk File** — upload a `.txt` file. One entry per line, format: `appid, appname`. Lines starting with `#` are skipped. A preview line appears after selecting the file showing how many apps were found.

```
# example apps.txt
APP001, Payments Service
APP002, Auth Gateway
# APP003, Deprecated App   <-- skipped
```

[SCREENSHOT 5 — Bulk File mode with a .txt file selected and the file preview count showing]

---

### Step 3 — Review options

| Option | Default | What it does |
|---|---|---|
| **Dry Run** | ✅ ON | Runs auth + GET, shows diff — no POST or PATCH |
| **Skip SSL** | ☐ OFF | Disables TLS verification (use only on internal networks) |
| **Log Level** | INFO | Set to DEBUG for full HTTP output when troubleshooting |

Credentials override and Advanced Options are collapsed by default — leave them blank to use `config.json`.

---

### Step 4 — Run dry first, then live

Click **Run cribl-pusher** with Dry Run checked. The right panel shows the unified diff — review it to confirm the routes look correct and are inserted above the catch-all.

[SCREENSHOT 6 — Dry run output panel: green "Completed successfully" banner and diff text visible]

Once satisfied, **uncheck Dry Run** and click **Run cribl-pusher** again. A green banner confirms success (exit code 0). A red banner means something failed — check the output for the error detail.

[SCREENSHOT 7 — Live run output panel: green success banner, command block, and output log]

---

## Tab 2 — ELK Roles + Cribl Routes (step by step)

Use this tab when an app needs **both** ELK security roles/role-mappings **and** Cribl routes created together in one run.

---

### Overview

[SCREENSHOT 8 — Tab 2 with all four panels visible: App Input, ELK Nonprod, ELK Prod, Cribl Workspace]

The tab has four input panels:

| Panel | What to fill in |
|---|---|
| **App Input** | App ID + App Name (or bulk file) |
| **ELK Connection — Nonprod** | Nonprod ELK URL + token or user/password |
| **ELK Connection — Prod** | Prod ELK URL + token or user/password |
| **Cribl Workspace** | Workspace, worker group, region, optional Cribl credentials |

---

### Step 1 — App Input

Same as Tab 1 — Single App or Bulk File. The App ID is used as the base name for ELK role names and Cribl route/destination IDs.

---

### Step 2 — ELK credentials

Fill in both the Nonprod and Prod ELK sections. A bearer token takes priority over username/password if both are provided. Leave blank to fall back to `config.json`.

---

### Step 3 — Cribl Workspace

Pick the workspace and worker group exactly as you would in Tab 1. For protected workspaces, the same amber banner appears — check **Allow production writes** before running.

---

### Step 4 — Options

| Option | Default | Notes |
|---|---|---|
| **Dry Run** | ✅ ON | No writes on either ELK or Cribl side |
| **Order** | ELK first | Change to Cribl first if needed |
| **Skip ELK** | ☐ | Skips ELK side — only pushes Cribl routes |
| **Skip Cribl** | ☐ | Skips Cribl side — only pushes ELK roles |

> Templates are always generated and saved to `ops_rm_r_templates_output/` regardless of the Skip flags — useful for review without making any API calls.

[SCREENSHOT 9 — Tab 2 Options panel with Skip ELK checked, showing only Cribl will run]

---

### Step 5 — Run

Click **Run rode_rm**. The right panel shows the command and combined output from both ELK and Cribl. A green banner means both sides completed successfully.

[SCREENSHOT 10 — Tab 2 output panel: command block and output log from a successful dry run]

---

## Credential resolution order

Credentials are resolved from highest to lowest priority:

```
1. UI field (Bearer Token / Username / Password)
2. Environment variable (CRIBL_TOKEN, CRIBL_USERNAME, CRIBL_PASSWORD)
3. config.json credentials block
```

Leave UI credential fields blank to fall back to `config.json`. This is the normal case for most users.

---

## Safety features

| Guard | What it does |
|---|---|
| **Dry Run (default on)** | Runs the full logic but never calls POST or PATCH |
| **Diff preview** | Always shows a unified diff before asking to write |
| **Minimum routes check** | Refuses to PATCH if the API returns fewer routes than the configured minimum |
| **No-shrink check** | Refuses to PATCH if the new route count would be less than the current count |
| **Duplicate skip** | Skips any app whose route or filter already exists |
| **require_allow** | Protected workspaces need "Allow production writes" checked |
| **Rollback snapshot** | Original route table is saved before every PATCH to `cribl_snapshots/{workspace}/` |

---

## Rolling back a change

If you need to undo a write, find the snapshot filename in the run output:

```
[SNAPSHOT] cribl_snapshots/prod/routes_snapshot_20240315T143022Z.json
```

Then restore it:

```bash
curl -k -X PATCH \
  "https://YOUR_CRIBL:9000/api/v1/m/{worker_group}/routes/{routes_table}" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d @cribl_snapshots/prod/routes_snapshot_20240315T143022Z.json
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Config file not found` | `config.json` is missing — copy from `config.example.json` |
| `login failed: 401` | Wrong credentials in `config.json` or token expired — generate a new one in Cribl UI under **Settings → API tokens** |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Check **Skip SSL** in the UI options |
| `[SAFETY] Refusing to PATCH: total_before=0` | The GET returned an empty route table — verify the base URL, worker group, and token permissions |
| Output panel is blank after clicking Run | Script exited before producing output — enable **Debug** log level and re-run |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` on the server |

---

## Contact / support

*(Add your team's Slack channel, email, or Jira project here)*

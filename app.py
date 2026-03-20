#!/usr/bin/env python3
"""
app.py — Flask UI wrapper for cribl-pusher.py and rode_rm.py

Run with:
    flask run --host=0.0.0.0 --port=5000
  or:
    python app.py

Environment variables:
    LOG_LEVEL   DEBUG / INFO / WARNING / ERROR  (default: INFO)
    LOG_FILE    Path to log file  (default: none, console only)
"""
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import traceback
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request

SCRIPT_DIR  = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.json"
PUSHER      = SCRIPT_DIR / "cribl-pusher.py"
RODE_RM     = SCRIPT_DIR / "rode_rm.py"


# ── Logging setup ──────────────────────────────────────────────────────────────

def setup_app_logging(app: Flask) -> logging.Logger:
    """
    Configure a dedicated 'flask.app' logger for the web layer.

    - Console handler always attached (stdout).
    - File handler attached when LOG_FILE env var is set
      (daily rotation, 30-day retention).
    - Flask's default werkzeug request logger is left intact but its
      level is raised to WARNING so it doesn't double-print every request.
    """
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        log_level = "INFO"

    fmt       = "%(asctime)s  %(levelname)-8s  [flask]  %(message)s"
    datefmt   = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt)

    logger = logging.getLogger("flask.app")
    logger.setLevel(getattr(logging, log_level))
    logger.handlers.clear()
    logger.propagate = False

    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File (optional)
    log_file = os.environ.get("LOG_FILE", "").strip()
    if log_file:
        fh = TimedRotatingFileHandler(
            log_file, when="midnight", backupCount=30, encoding="utf-8"
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        logger.info("File logging enabled: %s", log_file)

    # Silence werkzeug's per-request lines (we log our own)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    return logger


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

log = setup_app_logging(app)


# ── Request lifecycle hooks ────────────────────────────────────────────────────

@app.before_request
def _before():
    g.start_time = time.monotonic()
    log.info("→ %s %s  [%s]", request.method, request.path,
             request.remote_addr or "-")


@app.after_request
def _after(response):
    elapsed_ms = (time.monotonic() - g.start_time) * 1000
    level = logging.WARNING if response.status_code >= 400 else logging.INFO
    log.log(level, "← %s %s  %d  %.0fms",
            request.method, request.path,
            response.status_code, elapsed_ms)
    return response


# ── Unhandled exception handler — always return JSON, never bare HTML ──────────

@app.errorhandler(Exception)
def _handle_exception(exc):
    if isinstance(exc, SystemExit):
        # sys.exit() called inside a route (e.g. cribl die()) — treat as 500
        msg = f"Internal process exited unexpectedly (code={exc.code})"
    else:
        msg = str(exc)

    log.error("Unhandled exception on %s %s:\n%s",
              request.method, request.path,
              traceback.format_exc())
    return jsonify({"errors": [f"Server error: {msg}"]}), 500


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_subprocess(cmd: list, masked: str = "") -> tuple:
    log.info("  subprocess: %s", masked or " ".join(cmd))
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(SCRIPT_DIR),
    )
    log.info("  subprocess exit code: %d", result.returncode)
    if result.returncode != 0:
        log.warning("  subprocess failed — first 500 chars: %s",
                    (result.stdout or "")[:500])
    return result.stdout or "", result.returncode


def mask_cmd(cmd: list, sensitive: set) -> str:
    masked = [
        "***" if i > 0 and cmd[i - 1] in sensitive else part
        for i, part in enumerate(cmd)
    ]
    return " ".join(masked)


# ── Command builders ───────────────────────────────────────────────────────────

def build_pusher_cmd(form: dict, appfile_path: str) -> tuple:
    cmd = [
        sys.executable, str(PUSHER),
        "--yes",
        "--workspace",    form["workspace"],
        "--worker-group", form["worker_group"],
        "--region",       form["region"],
        "--log-level",    form.get("log_level", "INFO"),
        "--config",       str(CONFIG_PATH),
    ]

    if form.get("cribl_url", "").strip():
        cmd += ["--cribl-url", form["cribl_url"].strip()]
    if form.get("allow_prod"):
        cmd.append("--allow-prod")
    if form.get("dry_run"):
        cmd.append("--dry-run")
    if form.get("skip_ssl"):
        cmd.append("--skip-ssl")

    token    = form.get("token", "").strip()
    username = form.get("username", "").strip()
    password = form.get("password", "").strip()
    if token:
        cmd += ["--token", token]
    elif username and password:
        cmd += ["--username", username, "--password", password]

    if form.get("mode") == "bulk":
        cmd += ["--from-file", "--appfile", appfile_path or ""]
    else:
        cmd += ["--appid",   form.get("appid", "").strip(),
                "--appname", form.get("appname", "").strip()]

    group_id = form.get("group_id", "").strip()
    if group_id:
        cmd += ["--group-id", group_id]
        if form.get("create_missing_group"):
            cmd.append("--create-missing-group")
        if form.get("group_name", "").strip():
            cmd += ["--group-name", form["group_name"].strip()]

    if form.get("min_routes", "").strip():
        cmd += ["--min-existing-total-routes", form["min_routes"].strip()]
    if form.get("diff_lines", "").strip():
        cmd += ["--diff-lines", form["diff_lines"].strip()]
    if form.get("snapshot_dir", "").strip():
        cmd += ["--snapshot-dir", form["snapshot_dir"].strip()]
    if form.get("log_file", "").strip():
        cmd += ["--log-file", form["log_file"].strip()]

    sensitive = {"--password", "--token"}
    return cmd, mask_cmd(cmd, sensitive)


def build_remove_cmd(form: dict, appfile_path: str) -> tuple:
    cmd = [sys.executable, str(RODE_RM), "--yes", "--config", str(CONFIG_PATH)]

    if form.get("mode") == "bulk":
        cmd += ["--from-file", "--appfile", appfile_path or ""]
    else:
        cmd += ["--app_name", form.get("app_name", "").strip(),
                "--apmid",    form.get("apmid", "").strip()]

    cribl_token    = form.get("cribl_token", "").strip()
    cribl_username = form.get("cribl_username", "").strip()
    cribl_password = form.get("cribl_password", "").strip()
    if cribl_token:
        cmd += ["--token", cribl_token]
    elif cribl_username and cribl_password:
        cmd += ["--username", cribl_username, "--password", cribl_password]

    skip_elk = bool(form.get("skip_elk"))
    if not skip_elk:
        cmd += ["--elk-url", form.get("elk_url_nonprod", "").strip()]
        np_token = form.get("elk_token_nonprod", "").strip()
        np_user  = form.get("elk_user_nonprod", "").strip()
        np_pass  = form.get("elk_password_nonprod", "").strip()
        if np_token:
            cmd += ["--elk-token", np_token]
        elif np_user:
            cmd += ["--elk-user", np_user]
            if np_pass:
                cmd += ["--elk-password", np_pass]

        cmd += ["--elk-url-prod", form.get("elk_url_prod", "").strip()]
        p_token = form.get("elk_token_prod", "").strip()
        p_user  = form.get("elk_user_prod", "").strip()
        p_pass  = form.get("elk_password_prod", "").strip()
        if p_token:
            cmd += ["--elk-token-prod", p_token]
        elif p_user:
            cmd += ["--elk-user-prod", p_user]
            if p_pass:
                cmd += ["--elk-password-prod", p_pass]

    if form.get("cribl_url", "").strip():
        cmd += ["--cribl-url", form["cribl_url"].strip()]
    cmd += ["--workspace", form.get("workspace", "")]
    if form.get("worker_group", "").strip():
        cmd += ["--worker-group", form["worker_group"].strip()]
    if form.get("region", "").strip():
        cmd += ["--region", form["region"].strip()]
    if form.get("allow_prod"):
        cmd.append("--allow-prod")
    cmd += ["--order", form.get("order", "elk-first")]
    if skip_elk:
        cmd.append("--skip-elk")
    if form.get("skip_cribl"):
        cmd.append("--skip-cribl")
    if form.get("dry_run"):
        cmd.append("--dry-run")
    if form.get("skip_ssl"):
        cmd.append("--skip-ssl")
    cmd += ["--log-level", form.get("log_level", "INFO")]

    sensitive = {
        "--elk-password", "--elk-token",
        "--elk-password-prod", "--elk-token-prod",
        "--password", "--token",
    }
    return cmd, mask_cmd(cmd, sensitive)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
@app.route("/cribl")
@app.route("/cribl/")
def landing():
    return render_template("index.html")


@app.route("/cribl/app")
@app.route("/cribl/app/")
def app_page():
    try:
        config = load_config()
    except Exception as exc:
        log.error("Failed to load config.json: %s", exc)
        return f"Error loading config.json: {exc}", 500
    workspaces = {
        k: v for k, v in config.get("workspaces", {}).items()
        if not k.startswith("_")
    }
    return render_template("app.html", workspaces=workspaces, config=config)


@app.route("/health")
def health():
    return "ok", 200


@app.route("/cribl/api/run-pusher", methods=["POST"])
def run_pusher():
    form = request.form
    file = request.files.get("appfile")
    mode = form.get("mode", "single")

    errors = []
    if mode == "single":
        if not form.get("appid", "").strip():   errors.append("App ID is required.")
        if not form.get("appname", "").strip(): errors.append("App Name is required.")
    else:
        if not file or not file.filename:
            errors.append("Please upload an app list file (.txt).")

    worker_groups = form.getlist("worker_groups")
    if not worker_groups:
        errors.append("Select at least one worker group.")

    try:
        config = load_config()
    except Exception as exc:
        log.error("Config load error: %s", exc)
        return jsonify({"errors": [f"Could not load config.json: {exc}"]}), 500

    ws_cfg = config.get("workspaces", {}).get(form.get("workspace", ""), {})
    if ws_cfg.get("require_allow") and not form.get("allow_prod"):
        errors.append(
            f"Workspace '{form.get('workspace')}' requires the "
            "'Allow production writes' checkbox."
        )

    if errors:
        log.warning("run-pusher validation failed: %s", errors)
        return jsonify({"errors": errors}), 400

    log.info("run-pusher  workspace=%s  wgs=%s  mode=%s  dry_run=%s",
             form.get("workspace"), worker_groups, mode,
             bool(form.get("dry_run")))

    tmp_path = None
    try:
        if mode == "bulk" and file:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".txt", delete=False, dir=SCRIPT_DIR
            ) as tmp:
                file.save(tmp)
                tmp_path = tmp.name

        all_output = ""
        last_rc    = 0
        commands   = []

        for wg in worker_groups:
            form_dict = form.to_dict()
            form_dict["worker_group"] = wg
            cmd, masked = build_pusher_cmd(form_dict, tmp_path or "")
            commands.append({"wg": wg, "cmd": masked})
            output, rc = run_subprocess(cmd, masked)
            all_output += f"\n{'='*60}\n Worker group: {wg}\n{'='*60}\n{output}"
            if rc != 0:
                last_rc = rc

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return jsonify({
        "output":     all_output.strip(),
        "returncode": last_rc,
        "commands":   commands,
    })


@app.route("/cribl/api/run-remove", methods=["POST"])
def run_remove():
    form = request.form
    file = request.files.get("appfile")
    mode = form.get("mode", "single")

    errors    = []
    skip_elk   = bool(form.get("skip_elk"))
    skip_cribl = bool(form.get("skip_cribl"))

    if mode == "single":
        if not form.get("app_name", "").strip(): errors.append("App Name is required.")
        if not form.get("apmid", "").strip():    errors.append("App ID is required.")
    else:
        if not file or not file.filename:
            errors.append("Please upload an app list file (.txt).")

    if skip_elk and skip_cribl:
        errors.append("Nothing to do: both Skip ELK and Skip Cribl are checked.")

    if not skip_cribl and not form.get("worker_group", "").strip():
        errors.append("Worker Group is required when Cribl is not skipped.")

    if not skip_elk:
        if not form.get("elk_url_nonprod", "").strip():
            errors.append("ELK Nonprod URL is required.")
        if not form.get("elk_token_nonprod", "").strip() and not form.get("elk_user_nonprod", "").strip():
            errors.append("ELK Nonprod: provide User or Token.")
        if not form.get("elk_url_prod", "").strip():
            errors.append("ELK Prod URL is required.")
        if not form.get("elk_token_prod", "").strip() and not form.get("elk_user_prod", "").strip():
            errors.append("ELK Prod: provide User or Token.")

    try:
        config = load_config()
    except Exception as exc:
        log.error("Config load error: %s", exc)
        return jsonify({"errors": [f"Could not load config.json: {exc}"]}), 500

    ws_cfg = config.get("workspaces", {}).get(form.get("workspace", ""), {})
    if ws_cfg.get("require_allow") and not form.get("allow_prod"):
        errors.append(
            f"Workspace '{form.get('workspace')}' requires the "
            "'Allow production writes' checkbox."
        )

    if errors:
        log.warning("run-remove validation failed: %s", errors)
        return jsonify({"errors": errors}), 400

    log.info("run-remove  workspace=%s  wg=%s  mode=%s  skip_elk=%s  skip_cribl=%s  dry_run=%s",
             form.get("workspace"), form.get("worker_group"), mode,
             skip_elk, skip_cribl, bool(form.get("dry_run")))

    tmp_path = None
    try:
        if mode == "bulk" and file:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".txt", delete=False, dir=SCRIPT_DIR
            ) as tmp:
                file.save(tmp)
                tmp_path = tmp.name

        cmd, masked = build_remove_cmd(form.to_dict(), tmp_path or "")
        output, rc  = run_subprocess(cmd, masked)

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return jsonify({
        "output":     output,
        "returncode": rc,
        "command":    masked,
    })


if __name__ == "__main__":
    log.info("Starting Flask app on 0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)

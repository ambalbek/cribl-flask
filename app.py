#!/usr/bin/env python3
"""
app.py — Flask UI wrapper for cribl-pusher.py and rode_rm.py

Run with:
    flask run --host=0.0.0.0 --port=5000
  or:
    python app.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from flask import Flask, jsonify, render_template, request

SCRIPT_DIR  = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.json"
PUSHER      = SCRIPT_DIR / "cribl-pusher.py"
RODE_RM     = SCRIPT_DIR / "rode_rm.py"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_subprocess(cmd: list) -> tuple:
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
    return result.stdout or "", result.returncode


def mask_cmd(cmd: list, sensitive: set) -> str:
    masked = [
        "***" if i > 0 and cmd[i - 1] in sensitive else part
        for i, part in enumerate(cmd)
    ]
    return " ".join(masked)


# ── Command builders (mirrors ui.py logic exactly) ─────────────────────────────

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

    return cmd, mask_cmd(cmd, {"--password", "--token"})


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
        return jsonify({"errors": [f"Could not load config.json: {exc}"]}), 500

    ws_cfg = config.get("workspaces", {}).get(form.get("workspace", ""), {})
    if ws_cfg.get("require_allow") and not form.get("allow_prod"):
        errors.append(
            f"Workspace '{form.get('workspace')}' requires the "
            "'Allow production writes' checkbox."
        )

    if errors:
        return jsonify({"errors": errors}), 400

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
            output, rc = run_subprocess(cmd)
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
        return jsonify({"errors": [f"Could not load config.json: {exc}"]}), 500

    ws_cfg = config.get("workspaces", {}).get(form.get("workspace", ""), {})
    if ws_cfg.get("require_allow") and not form.get("allow_prod"):
        errors.append(
            f"Workspace '{form.get('workspace')}' requires the "
            "'Allow production writes' checkbox."
        )

    if errors:
        return jsonify({"errors": errors}), 400

    tmp_path = None
    try:
        if mode == "bulk" and file:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".txt", delete=False, dir=SCRIPT_DIR
            ) as tmp:
                file.save(tmp)
                tmp_path = tmp.name

        cmd, masked = build_remove_cmd(form.to_dict(), tmp_path or "")
        output, rc  = run_subprocess(cmd)

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
    app.run(host="0.0.0.0", port=5000, debug=False)

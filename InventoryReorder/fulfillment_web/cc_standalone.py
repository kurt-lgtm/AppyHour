"""Standalone Command Center — audit/dev mode without full fulfillment app."""

from __future__ import annotations

from flask import Flask, jsonify, request, send_from_directory
from pathlib import Path

import command_center

HERE = Path(__file__).parent
app = Flask(__name__, static_folder=None)


# ── Static files ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return (HERE / "templates" / "cc_standalone.html").read_text()


@app.route("/static/command-center/<path:filename>")
def cc_static(filename):
    return send_from_directory(HERE / "static" / "command-center", filename)


# ── CC API (mirrors app.py routes) ────────────────────────────────────────

@app.route("/api/cc/tasks", methods=["GET"])
def cc_list_tasks():
    status = request.args.get("status")
    type_ = request.args.get("type")
    day = request.args.get("day_of_week")
    return jsonify(command_center.list_tasks(status=status, type=type_, day_of_week=day))


@app.route("/api/cc/tasks", methods=["POST"])
def cc_create_task():
    data = request.json
    title = data.pop("title")
    type_ = data.pop("type", "work")
    checklist = data.pop("checklist", None)
    return jsonify(command_center.create_task(title, type_, checklist=checklist, **data))


@app.route("/api/cc/tasks/<task_id>", methods=["GET"])
def cc_get_task(task_id):
    task = command_center.get_task(task_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    return jsonify(task)


@app.route("/api/cc/tasks/<task_id>", methods=["PATCH"])
def cc_update_task(task_id):
    task = command_center.update_task(task_id, **request.json)
    return jsonify(task)


@app.route("/api/cc/tasks/<task_id>", methods=["DELETE"])
def cc_delete_task(task_id):
    command_center.delete_task(task_id)
    return jsonify({"ok": True})


@app.route("/api/cc/tasks/<task_id>/checklist", methods=["GET"])
def cc_checklist(task_id):
    return jsonify(command_center.get_checklist(task_id))


@app.route("/api/cc/tasks/<task_id>/checklist", methods=["POST"])
def cc_add_checklist(task_id):
    data = request.json
    item = command_center.add_checklist_item(task_id, data["title"], data.get("position"))
    return jsonify(item)


@app.route("/api/cc/checklist/<item_id>/toggle", methods=["POST"])
def cc_toggle_checklist(item_id):
    item = command_center.toggle_checklist_item(item_id)
    return jsonify(item)


@app.route("/api/cc/tasks/<task_id>/checklist/reorder", methods=["POST"])
def cc_reorder_checklist(task_id):
    command_center.reorder_checklist(task_id, request.json["item_ids"])
    return jsonify({"ok": True})


@app.route("/api/cc/recurring", methods=["GET"])
def cc_list_recurring():
    return jsonify(command_center.list_recurring())


@app.route("/api/cc/recurring", methods=["POST"])
def cc_create_recurring():
    data = dict(request.json)
    title = data.pop("title")
    day = data.pop("day_of_week")
    return jsonify(command_center.create_recurring(title, day, **data))


@app.route("/api/cc/recurring/<rec_id>", methods=["PATCH"])
def cc_update_recurring(rec_id):
    rec = command_center.update_recurring(rec_id, **request.json)
    return jsonify(rec)


@app.route("/api/cc/recurring/<rec_id>", methods=["DELETE"])
def cc_delete_recurring(rec_id):
    command_center.delete_recurring(rec_id)
    return jsonify({"ok": True})


@app.route("/api/cc/recurring/spawn", methods=["POST"])
def cc_spawn_recurring():
    energy = (request.json or {}).get("energy", "medium")
    spawned = command_center.spawn_today_recurring(energy)
    return jsonify(spawned)


@app.route("/api/cc/blockers", methods=["GET"])
def cc_blockers():
    return jsonify(command_center.get_active_blockers())


@app.route("/api/cc/blockers", methods=["POST"])
def cc_create_blocker():
    data = dict(request.json)
    task_id = data.pop("task_id")
    type_ = data.pop("type")
    return jsonify(command_center.create_blocker(task_id, type_, **data))


@app.route("/api/cc/blockers/<blocker_id>/resolve", methods=["POST"])
def cc_resolve_blocker(blocker_id):
    return jsonify(command_center.resolve_blocker(blocker_id))


@app.route("/api/cc/today", methods=["GET"])
def cc_today():
    energy = request.args.get("energy", "medium")
    return jsonify(command_center.get_today_tasks(energy))


@app.route("/api/cc/brief", methods=["GET"])
def cc_brief():
    brief = command_center.get_morning_brief()
    if brief is None:
        return jsonify({"status": "no brief today"})
    return jsonify(brief)


@app.route("/api/cc/brief", methods=["POST"])
def cc_post_brief():
    command_center.store_morning_brief(request.json)
    return jsonify({"status": "stored"})


@app.route("/api/cc/build-brief", methods=["POST"])
def cc_build_brief():
    external = request.get_json(silent=True) or {}
    brief = command_center.build_morning_brief(inventory=None, external=external)
    return jsonify(brief)


@app.route("/api/cc/streaks", methods=["GET"])
def cc_streaks():
    return jsonify(command_center.get_streaks())


@app.route("/api/cc/stats", methods=["GET"])
def cc_stats():
    return jsonify(command_center.get_daily_stats())


@app.route("/api/cc/slack-trawl", methods=["POST"])
def cc_slack_trawl():
    messages = request.json.get("messages", [])
    created = command_center.process_slack_trawl(messages)
    return jsonify(created)


@app.route("/api/cc/decisions", methods=["GET"])
def cc_get_decisions():
    return jsonify(command_center.get_pending_decisions())


@app.route("/api/cc/decisions", methods=["POST"])
def cc_create_decision():
    body = request.get_json(silent=True) or {}
    d = command_center.create_decision(
        question=body.get("question", ""),
        options=body.get("options"),
        context=body.get("context", ""),
        source=body.get("source", "system"),
    )
    return jsonify(d)


@app.route("/api/cc/decisions/<did>/answer", methods=["POST"])
def cc_answer_decision(did):
    body = request.get_json(silent=True) or {}
    d = command_center.answer_decision(did, body.get("answer", ""))
    if d is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(d)


@app.route("/api/cc/activity", methods=["GET"])
def cc_activity():
    limit = request.args.get("limit", 50, type=int)
    return jsonify(command_center.get_activity_log(limit))


@app.route("/api/cc/search", methods=["GET"])
def cc_search():
    q = request.args.get("q", "")
    return jsonify(command_center.global_search(q))


@app.route("/api/cc/recurring-grid", methods=["GET"])
def cc_recurring_grid():
    return jsonify(command_center.get_recurring_grid())


@app.route("/api/cc/health", methods=["GET"])
def cc_health():
    return jsonify(command_center.health_check())


@app.route("/api/cc/eod", methods=["GET"])
def cc_eod():
    return jsonify(command_center.get_eod_summary())


@app.route("/api/cc/weekly-review", methods=["GET"])
def cc_weekly_review():
    return jsonify(command_center.get_weekly_review())


@app.route("/api/cc/carryovers", methods=["GET"])
def cc_carryovers():
    return jsonify(command_center.get_carryover_tasks())


@app.route("/api/cc/triage", methods=["POST"])
def cc_triage():
    body = request.get_json(silent=True) or {}
    result = command_center.triage_task(body.get("task_id", ""), body.get("action", "keep"))
    if result is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(result)


if __name__ == "__main__":
    import sys
    port = 5188
    print(f"Command Center standalone: http://127.0.0.1:{port}")
    if "--browser" in sys.argv:
        import webbrowser
        import threading
        threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port)

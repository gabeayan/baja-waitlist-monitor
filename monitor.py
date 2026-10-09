#!/usr/bin/env python3
"""Watch Baja SAE waitlists and push a phone notification (via ntfy.sh) when they change.

State is kept in state.json so each run compares against the previous one.
Set NTFY_TOPIC in the environment. Use --dry-run to print instead of sending.
"""
import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUR_TEAM_NUMBER = "100"  # Stevens Baja
BASE = "https://www.bajasae.net/cdsweb/reg/CompAttendance.aspx?CompetitionID="
EVENTS = {
    "Williamsport": BASE + "1799473b-3ba6-44e4-bc4f-420bdd3031c0",
    "Michigan": BASE + "28e53ae9-4171-4056-bc9e-e277893c6e0d",
}
STATE_FILE = Path(__file__).with_name("state.json")
DRY_RUN = "--dry-run" in sys.argv


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (waitlist monitor)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def cell_text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_table(page, table_id):
    m = re.search(r'(?is)<table[^>]*id="%s"[^>]*>(.*?)</table>' % table_id, page)
    if not m:
        return None
    body = re.search(r"(?is)<tbody[^>]*>(.*?)</tbody>", m.group(1))
    rows = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", body.group(1) if body else m.group(1)):
        cells = [cell_text(c) for c in re.findall(r"(?is)<td[^>]*>(.*?)</td>", tr)]
        if cells:
            rows.append(cells)
    return rows


def snapshot(url):
    page = fetch(url)
    registered = parse_table(page, "dataTableRegistered")
    waitlisted = parse_table(page, "dataTableWaitlisted")
    if registered is None or waitlisted is None or not registered:
        raise RuntimeError("couldn't find team tables on page")
    # Registered rows: Team #, School, Team, Class. Waitlist rows: Position, Team #, School, Team, Class.
    return {
        "registered": [{"num": r[0], "school": r[1]} for r in registered],
        "waitlist": [{"pos": w[0], "num": w[1], "school": w[2]} for w in waitlisted if len(w) >= 3],
    }


def our_spot(snap):
    for i, w in enumerate(snap["waitlist"]):
        if w["num"] == OUR_TEAM_NUMBER:
            return i + 1, w["pos"]
    return None, None


def describe_changes(name, old, new):
    old_wl = {w["num"]: w["school"] for w in old["waitlist"]}
    new_wl = {w["num"]: w["school"] for w in new["waitlist"]}
    old_reg = {r["num"]: r["school"] for r in old["registered"]}
    new_reg = {r["num"]: r["school"] for r in new["registered"]}

    promoted = [old_wl[n] for n in old_wl if n not in new_wl and n in new_reg]
    left_wl = [old_wl[n] for n in old_wl if n not in new_wl and n not in new_reg]
    dropped_reg = [old_reg[n] for n in old_reg if n not in new_reg]
    joined_wl = [new_wl[n] for n in new_wl if n not in old_wl]
    newly_reg = [new_reg[n] for n in new_reg if n not in old_reg and n not in old_wl]

    lines = []
    if OUR_TEAM_NUMBER in new_reg and OUR_TEAM_NUMBER not in old_reg:
        lines.append("🎉 STEVENS IS OFF THE WAITLIST AND REGISTERED!")
    for s in promoted:
        lines.append(f"⬆️ Moved off waitlist into event: {s}")
    for s in left_wl:
        lines.append(f"🚪 Left the waitlist: {s}")
    for s in dropped_reg:
        lines.append(f"❌ Dropped from registered list: {s}")
    for s in newly_reg:
        lines.append(f"➕ Newly registered: {s}")
    for s in joined_wl:
        lines.append(f"🕒 Joined waitlist: {s}")
    if not lines and [w["num"] for w in old["waitlist"]] != [w["num"] for w in new["waitlist"]]:
        lines.append("Waitlist order changed.")
    if not lines:
        return None

    old_ahead, _ = our_spot(old)
    ahead, pos = our_spot(new)
    if ahead:
        delta = f" (was #{old_ahead})" if old_ahead and old_ahead != ahead else ""
        lines.append(f"\nStevens: #{ahead} in line{delta}, {ahead - 1} teams ahead. "
                     f"Registered {len(new['registered'])}, waitlist {len(new['waitlist'])}.")
    return "\n".join(lines)


def notify(title, message, url, priority="default"):
    body = f"{message}\n\n{url}"
    if DRY_RUN:
        print(f"--- {title} [{priority}]\n{body}\n")
        return
    topic = os.environ["NTFY_TOPIC"]
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={"Title": title.encode("utf-8"), "Click": url, "Priority": priority, "Tags": "racing_car"},
    )
    urllib.request.urlopen(req, timeout=30).read()


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    changed = False
    for name, url in EVENTS.items():
        try:
            new = snapshot(url)
        except Exception as e:  # site hiccup: skip this run, keep old state
            print(f"{name}: fetch/parse failed: {e}", file=sys.stderr)
            continue
        old = state.get(name)
        ahead, _ = our_spot(new)
        print(f"{name}: {len(new['registered'])} registered, {len(new['waitlist'])} waitlisted, Stevens #{ahead}")
        if old is None:
            notify(f"Baja {name} monitor started",
                   f"Watching the waitlist. Stevens is #{ahead} in line ({ahead - 1} teams ahead)." if ahead
                   else "Watching the waitlist. Stevens isn't on the waitlist.", url, "low")
        else:
            msg = describe_changes(name, old, new)
            if msg:
                urgent = "STEVENS IS OFF" in msg
                notify(f"Baja {name} waitlist changed", msg, url, "urgent" if urgent else "high")
        if old != new:
            state[name] = new
            changed = True

    # Monthly heartbeat: a status ping, and a commit that keeps GitHub from pausing the schedule.
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    if state.get("_heartbeat") != month and all(n in state for n in EVENTS):
        if "_heartbeat" in state:
            parts = []
            for name in EVENTS:
                ahead, _ = our_spot(state[name])
                parts.append(f"{name}: Stevens #{ahead} in line" if ahead else f"{name}: not on waitlist")
            notify("Baja waitlist monitor: still watching", "\n".join(parts), EVENTS["Williamsport"], "low")
        state["_heartbeat"] = month
        changed = True

    if changed and not DRY_RUN:
        STATE_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()

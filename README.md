# Baja SAE waitlist monitor

Checks the Williamsport and Michigan team lists every ~5 minutes (GitHub Actions) and sends a
push notification through [ntfy](https://ntfy.sh) when either waitlist changes. Each alert
includes the page link, and tapping the notification opens it.

- `monitor.py`: fetches both pages, compares them with `state.json`, sends alerts
- `.github/workflows/monitor.yml`: the schedule
- Secret `NTFY_TOPIC`: the private ntfy topic name your phone subscribes to

Test locally without sending anything: `python3 monitor.py --dry-run`

To stop it: GitHub repo → Actions → "Baja waitlist monitor" → ⋯ → Disable workflow.

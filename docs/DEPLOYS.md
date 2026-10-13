# Deploys

Render auto-deploys `main` from `render.yaml`. The web service has a persistent disk. Render's own rule is that a disk disables zero-downtime deploys: the old instance is stopped, the disk is attached to the new instance, and the site is down until the new process passes `/healthz`. That gap has been 1–2 minutes. Health checks cannot remove it while the disk is attached. Removing the disk would require the JSON store to live somewhere else first (see `docs/DURABLE_STORAGE.md`); this deploy does not do that.

## What happens now

1. **Regular US cash session (weekdays 09:30–16:00 America/New_York).** `backend/scripts/market_hours_deploy_gate.py` is the web service `preDeployCommand`. It exits 1, Render aborts the deploy, and the instance already running keeps serving. A push during the session does not go live by itself. After 16:00 ET, press Manual Deploy in the Render dashboard (or push again). Early closes are not special-cased.
2. **Hotfix during the session.** Set `PLUTO_ALLOW_MARKET_HOURS_DEPLOY=1` on the web service, deploy, then remove the variable. The disk swap still causes a gap. Do this only when the running build is worse than a short outage.
3. **Outside that window.** The gate exits 0. Render stops the old instance (`maxShutdownDelaySeconds` 90, gunicorn `--graceful-timeout 30`) and starts the new one. Expect a gap of however long the new process takes to pass `/healthz`. The 5-minute cron retries a connection error or HTTP 502/503/504 twice before failing the job. The 10-second monitor worker already backs off and tries again.
4. **`/healthz`** stays a liveness check with no broker or disk work, so a slow scan cannot fail the health check by itself (gunicorn is `gthread`).

Extended hours (04:00–09:30 and 16:00–20:00 ET) are not inside the gate. The monitor still runs then. A deploy in that window has the same disk gap.

## What this does not cost

No extra Render service. An edge proxy in front of the disk service could hide the gap for browsers, and would be another web service (Render Starter; confirm the current price, historically about $7/month). It is not in `render.yaml`, so a blueprint sync will not create one.

## What Tre does

Nothing for the gate: it ships in `render.yaml`. Optional: `PLUTO_ALLOW_MARKET_HOURS_DEPLOY` only for a hotfix, then delete it. Off-site backup variables are separate; see `docs/DURABLE_STORAGE.md`.

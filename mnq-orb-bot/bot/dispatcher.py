"""
Automated Workflow Dispatcher.

Monitors Supabase for trigger conditions and dispatches the appropriate
ruflo workflow or claude -p headless session automatically.

This is the "nervous system" that connects bot runtime state to the
ruflo analysis/recalibration layer. The bot writes to Supabase (vanilla Python).
This dispatcher reads Supabase and triggers ruflo workflows.

Runs as a lightweight daemon alongside the trading bot:
    python -m bot.dispatcher

Or via ruflo daemon:
    npx ruflo@latest daemon start  (includes this as a worker)
"""

import os
import json
import subprocess
import logging
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — all trigger thresholds in one place
# ---------------------------------------------------------------------------

TRIGGERS = {
    # Losing streak → auto-debug
    "debug_on_losing_streak": {
        "enabled": True,
        "consecutive_losses": 3,        # Trigger after 3 consecutive losses
        "cooldown_hours": 24,           # Don't re-trigger within 24 hours
    },

    # Weekly performance degradation → auto-debug
    "debug_on_pf_decay": {
        "enabled": True,
        "rolling_window_days": 7,
        "pf_threshold": 1.3,           # If weekly PF drops below 1.3
        "min_trades": 10,              # Need at least 10 trades in window
        "cooldown_hours": 168,          # Once per week max
    },

    # Monthly recalibration → auto-recalibrate
    "recalibrate_monthly": {
        "enabled": True,
        "day_of_month": "first_saturday",
        "time_utc": "12:00",
    },

    # Paper trade milestone → auto-eval-consensus
    "eval_consensus_at_milestone": {
        "enabled": True,
        "trade_count": 50,             # Trigger at 50 paper trades
        "source": "paper",
    },

    # Crowding spike → alert + suggest pause
    "crowding_alert": {
        "enabled": True,
        "check_day": "sunday",
        "multiplier_threshold": 2.0,   # 2x rolling average
    },

    # 4 consecutive weeks of PF < 1.3 → halt recommendation
    "halt_on_sustained_decay": {
        "enabled": True,
        "weeks": 4,
        "pf_threshold": 1.3,
    },
}


# ---------------------------------------------------------------------------
# Supabase state readers
# ---------------------------------------------------------------------------

def _get_supabase():
    """Lazy Supabase client init."""
    from supabase import create_client
    return create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


def get_recent_trades(days: int = 7, source: str = "live") -> list[dict]:
    """Fetch recent trades from Supabase."""
    sb = _get_supabase()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    result = sb.table("trades") \
        .select("*") \
        .eq("source", source) \
        .gte("entry_time", cutoff) \
        .order("entry_time", desc=True) \
        .execute()
    return result.data or []


def get_consecutive_losses(source: str = "live") -> int:
    """Count current consecutive loss streak."""
    sb = _get_supabase()
    result = sb.table("trades") \
        .select("pnl_dollars") \
        .eq("source", source) \
        .order("exit_time", desc=True) \
        .limit(20) \
        .execute()

    streak = 0
    for trade in (result.data or []):
        if trade["pnl_dollars"] <= 0:
            streak += 1
        else:
            break
    return streak


def get_weekly_profit_factor(source: str = "live") -> Optional[float]:
    """Calculate profit factor over the last 7 days."""
    trades = get_recent_trades(days=7, source=source)
    if len(trades) < TRIGGERS["debug_on_pf_decay"]["min_trades"]:
        return None

    gross_profit = sum(t["pnl_dollars"] for t in trades if t["pnl_dollars"] > 0)
    gross_loss = abs(sum(t["pnl_dollars"] for t in trades if t["pnl_dollars"] <= 0))

    if gross_loss == 0:
        return float("inf")
    return gross_profit / gross_loss


def get_trade_count(source: str = "paper") -> int:
    """Count total trades for a given source."""
    sb = _get_supabase()
    result = sb.table("trades") \
        .select("id", count="exact") \
        .eq("source", source) \
        .execute()
    return result.count or 0


def get_last_dispatch(workflow: str) -> Optional[datetime]:
    """Check when a workflow was last dispatched."""
    sb = _get_supabase()
    result = sb.table("system_state") \
        .select("value") \
        .eq("key", f"last_dispatch:{workflow}") \
        .execute()

    if result.data and result.data[0].get("value"):
        ts = json.loads(result.data[0]["value"])
        return datetime.fromisoformat(ts)
    return None


def record_dispatch(workflow: str):
    """Record that a workflow was dispatched."""
    sb = _get_supabase()
    sb.table("system_state").upsert({
        "key": f"last_dispatch:{workflow}",
        "value": json.dumps(datetime.utcnow().isoformat()),
        "updated_at": datetime.utcnow().isoformat(),
    }).execute()


# ---------------------------------------------------------------------------
# Workflow dispatchers
# ---------------------------------------------------------------------------

def dispatch_claude_command(command: str, description: str):
    """Dispatch a slash command via claude -p (headless mode).

    This runs Claude Code non-interactively with the project context,
    executes the command, and captures output.
    """
    project_dir = Path(__file__).parent.parent
    logger.info(f"Dispatching: {description}")

    try:
        result = subprocess.run(
            ["claude", "-p", "--max-budget-usd", "2.00",
             f"Run the /{command} slash command. Project dir: {project_dir}"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )

        if result.returncode == 0:
            logger.info(f"Dispatch success: {command}")
            # Store output summary in memory
            _store_dispatch_result(command, result.stdout[:2000])
        else:
            logger.error(f"Dispatch failed: {command}\n{result.stderr[:500]}")

    except subprocess.TimeoutExpired:
        logger.error(f"Dispatch timeout: {command}")
    except FileNotFoundError:
        logger.error("claude CLI not found — is Claude Code installed?")


def dispatch_ruflo_hook(hook_command: str, description: str):
    """Dispatch a ruflo hook directly (lighter than full claude -p)."""
    logger.info(f"Ruflo hook: {description}")
    try:
        subprocess.run(
            hook_command.split(),
            capture_output=True, text=True, timeout=120,
        )
    except Exception as e:
        logger.error(f"Ruflo hook failed: {e}")


def dispatch_slack_alert(message: str):
    """Send a Slack alert."""
    try:
        from slack_sdk import WebClient
        client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
        client.chat_postMessage(
            channel=os.getenv("SLACK_CHANNEL_ID"),
            text=message,
        )
    except Exception as e:
        logger.error(f"Slack alert failed: {e}")


def _store_dispatch_result(workflow: str, output: str):
    """Store dispatch result in ruflo memory."""
    try:
        subprocess.run([
            "npx", "ruflo@latest", "memory", "store",
            "--namespace", "mnq-bot",
            "--key", f"dispatch:{workflow}:{datetime.utcnow().strftime('%Y%m%d')}",
            "--value", output[:1000],
            "--reasoningbank",
        ], capture_output=True, timeout=30)
    except Exception:
        pass  # Memory storage is best-effort


# ---------------------------------------------------------------------------
# Trigger evaluators
# ---------------------------------------------------------------------------

def check_losing_streak():
    """Trigger /debug if consecutive losses exceed threshold."""
    cfg = TRIGGERS["debug_on_losing_streak"]
    if not cfg["enabled"]:
        return

    streak = get_consecutive_losses()
    if streak >= cfg["consecutive_losses"]:
        last = get_last_dispatch("debug_streak")
        if last and (datetime.utcnow() - last).total_seconds() < cfg["cooldown_hours"] * 3600:
            return  # In cooldown

        dispatch_slack_alert(
            f"⚠️ {streak} consecutive losses detected. Running auto-debug analysis."
        )
        dispatch_claude_command(
            "debug",
            f"Auto-debug: {streak} consecutive losses"
        )
        record_dispatch("debug_streak")


def check_pf_decay():
    """Trigger /debug if weekly profit factor drops below threshold."""
    cfg = TRIGGERS["debug_on_pf_decay"]
    if not cfg["enabled"]:
        return

    pf = get_weekly_profit_factor()
    if pf is None:
        return  # Not enough trades

    if pf < cfg["pf_threshold"]:
        last = get_last_dispatch("debug_pf")
        if last and (datetime.utcnow() - last).total_seconds() < cfg["cooldown_hours"] * 3600:
            return

        dispatch_slack_alert(
            f"⚠️ Weekly profit factor dropped to {pf:.2f} (threshold: {cfg['pf_threshold']}). Running analysis."
        )
        dispatch_claude_command(
            "debug",
            f"Auto-debug: weekly PF at {pf:.2f}"
        )
        record_dispatch("debug_pf")


def check_monthly_recalibration():
    """Trigger /recalibrate on the first Saturday of each month."""
    cfg = TRIGGERS["recalibrate_monthly"]
    if not cfg["enabled"]:
        return

    now = datetime.utcnow()
    # First Saturday: day 1-7 and weekday == 5 (Saturday)
    if now.day <= 7 and now.weekday() == 5:
        last = get_last_dispatch("recalibrate")
        if last and (now - last).days < 25:
            return  # Already ran this month

        dispatch_slack_alert("📊 Monthly recalibration starting.")
        dispatch_claude_command("recalibrate", "Monthly auto-recalibration")
        record_dispatch("recalibrate")


def check_consensus_milestone():
    """Trigger /eval-consensus when paper trade count hits milestone."""
    cfg = TRIGGERS["eval_consensus_at_milestone"]
    if not cfg["enabled"]:
        return

    count = get_trade_count(source=cfg["source"])
    if count >= cfg["trade_count"]:
        last = get_last_dispatch("eval_consensus")
        if last:
            return  # Already evaluated

        dispatch_slack_alert(
            f"🎯 {count} paper trades reached. Evaluating consensus signal validation."
        )
        dispatch_claude_command("eval-consensus", f"Consensus evaluation at {count} trades")
        record_dispatch("eval_consensus")


def check_crowding():
    """Run crowding monitor on Sundays."""
    cfg = TRIGGERS["crowding_alert"]
    if not cfg["enabled"]:
        return

    now = datetime.utcnow()
    if now.weekday() != 6:  # Sunday = 6
        return

    last = get_last_dispatch("crowding")
    if last and (now - last).days < 6:
        return

    try:
        from research.crowding_monitor import count_mentions
        data = count_mentions()

        # TODO: compare against rolling average from Supabase
        # For now, just log and store
        logger.info(f"Crowding check: {data['orb_mentions']} ORB mentions")
        record_dispatch("crowding")

    except Exception as e:
        logger.error(f"Crowding check failed: {e}")


def check_sustained_decay():
    """Halt recommendation if PF < threshold for N consecutive weeks."""
    cfg = TRIGGERS["halt_on_sustained_decay"]
    if not cfg["enabled"]:
        return

    # Check last N weeks of weekly PF
    sb = _get_supabase()
    weeks_below = 0

    for week_offset in range(cfg["weeks"]):
        start = datetime.utcnow() - timedelta(weeks=week_offset + 1)
        end = datetime.utcnow() - timedelta(weeks=week_offset)

        result = sb.table("trades") \
            .select("pnl_dollars") \
            .eq("source", "live") \
            .gte("exit_time", start.isoformat()) \
            .lt("exit_time", end.isoformat()) \
            .execute()

        trades = result.data or []
        if len(trades) < 5:
            return  # Not enough data

        gross_profit = sum(t["pnl_dollars"] for t in trades if t["pnl_dollars"] > 0)
        gross_loss = abs(sum(t["pnl_dollars"] for t in trades if t["pnl_dollars"] <= 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        if pf < cfg["pf_threshold"]:
            weeks_below += 1
        else:
            break

    if weeks_below >= cfg["weeks"]:
        dispatch_slack_alert(
            f"🛑 HALT RECOMMENDATION: Profit factor below {cfg['pf_threshold']} for "
            f"{weeks_below} consecutive weeks. Consider pausing the bot and running /recalibrate."
        )
        record_dispatch("halt_recommendation")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_all_checks():
    """Run all trigger checks once."""
    logger.info("Running dispatch checks...")

    check_losing_streak()
    check_pf_decay()
    check_monthly_recalibration()
    check_consensus_milestone()
    check_crowding()
    check_sustained_decay()

    logger.info("Dispatch checks complete.")


def run_daemon(interval_minutes: int = 30):
    """Run as a daemon, checking triggers every N minutes.

    In production, this runs alongside the trading bot on the VPS.
    The bot trades; the dispatcher monitors and triggers analysis.
    """
    import time as time_module

    logger.info(f"Dispatcher daemon started. Checking every {interval_minutes} minutes.")

    while True:
        try:
            run_all_checks()
        except Exception as e:
            logger.error(f"Dispatcher error: {e}")

        time_module.sleep(interval_minutes * 60)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    import sys
    if "--daemon" in sys.argv:
        interval = 30
        for i, arg in enumerate(sys.argv):
            if arg == "--interval" and i + 1 < len(sys.argv):
                interval = int(sys.argv[i + 1])
        run_daemon(interval)
    else:
        run_all_checks()

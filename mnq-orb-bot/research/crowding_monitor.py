"""
ORB Strategy Crowding Monitor.

Tracks how often ORB/opening range strategies are discussed on Reddit.
When a strategy gets popular, its edge decays. This is the early warning.

Runs weekly via cron. Writes one row to Supabase. Alerts via Slack if
discussion volume spikes >2x over the 4-week rolling average.

Usage:
    python -m research.crowding_monitor

Cron (every Sunday at 10am ET):
    0 10 * * 0 cd /path/to/mnq-orb-bot && python -m research.crowding_monitor
"""

import os
import praw
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger(__name__)

SUBREDDITS = ["daytrading", "futurestrading"]
KEYWORDS = ["ORB", "opening range", "9EMA", "9 EMA", "MNQ", "opening range breakout"]
LOOKBACK_DAYS = 7  # Count posts from the last 7 days


def count_mentions() -> dict:
    """Count keyword mentions across target subreddits for the past week."""
    reddit = praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID"),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        user_agent=os.getenv("REDDIT_USER_AGENT", "mnq-orb-bot/0.1"),
    )

    counts = {kw: 0 for kw in KEYWORDS}
    total_posts = 0

    for sub_name in SUBREDDITS:
        try:
            subreddit = reddit.subreddit(sub_name)
            for post in subreddit.new(limit=500):
                age = datetime.utcnow() - datetime.utcfromtimestamp(post.created_utc)
                if age > timedelta(days=LOOKBACK_DAYS):
                    break

                total_posts += 1
                text = f"{post.title} {post.selftext}".lower()

                for kw in KEYWORDS:
                    if kw.lower() in text:
                        counts[kw] += 1

        except Exception as e:
            logger.error(f"Error scraping r/{sub_name}: {e}")

    orb_mentions = counts.get("ORB", 0) + counts.get("opening range", 0) + counts.get("opening range breakout", 0)

    result = {
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "orb_mentions": orb_mentions,
        "ema_mentions": counts.get("9EMA", 0) + counts.get("9 EMA", 0),
        "mnq_mentions": counts.get("MNQ", 0),
        "total_posts_scanned": total_posts,
    }

    logger.info(f"Crowding check: {orb_mentions} ORB mentions in {total_posts} posts")
    return result


def check_and_alert(current: dict) -> None:
    """Compare current mentions to rolling average and alert if spiking."""
    # TODO: Query last 4 weeks from Supabase, calculate rolling average
    # If current orb_mentions > 2x rolling average, send Slack alert
    #
    # For now, just log. Wire up Supabase + Slack in Phase 3.
    logger.info(f"Crowding data: {json.dumps(current)}")

    # Placeholder for Supabase insert:
    # supabase.table("crowding_monitor").insert(current).execute()

    # Placeholder for Slack alert:
    # if current["orb_mentions"] > 2 * rolling_avg:
    #     slack.chat_postMessage(channel=CHANNEL, text=f"⚠️ Edge decay warning: ORB mentions up {ratio:.1f}x")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    data = count_mentions()
    check_and_alert(data)

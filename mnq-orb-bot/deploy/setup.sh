#!/bin/bash
# Deploy MNQ ORB Bot to Hetzner VPS
# Run once after initial VPS setup (Ubuntu 24)
# Usage: bash deploy/setup.sh

set -euo pipefail

echo "=== MNQ ORB Bot — VPS Setup ==="

# 1. System deps
echo "Installing system dependencies..."
sudo apt update && sudo apt install -y python3.12 python3.12-venv python3-pip nodejs npm

# 2. Project
echo "Setting up project..."
sudo mkdir -p /opt/mnq-orb-bot
sudo chown deploy:deploy /opt/mnq-orb-bot
cp -r . /opt/mnq-orb-bot/
cd /opt/mnq-orb-bot

# 3. Python env
echo "Creating Python environment..."
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 4. Environment
echo "Setting up environment..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️  Edit /opt/mnq-orb-bot/.env with your API keys before starting!"
fi

# 5. Ruflo (factory tooling — not required for bot runtime)
echo "Installing ruflo..."
npm install -g ruflo@latest || echo "ruflo install failed — non-critical, bot works without it"
npx ruflo@latest init 2>/dev/null || true
npx ruflo@latest plugins install @claude-flow/plugin-agentic-qe 2>/dev/null || true
npx ruflo@latest embeddings init --model all-mpnet-base-v2 2>/dev/null || true

# 6. Claude Code (for headless dispatch)
echo "Checking claude CLI..."
which claude >/dev/null 2>&1 || echo "⚠️  Install Claude Code CLI for auto-dispatch: https://docs.anthropic.com/claude-code"

# 7. Systemd services
echo "Installing systemd services..."
sudo cp deploy/mnq-bot.service /etc/systemd/system/
sudo cp deploy/mnq-dispatcher.service /etc/systemd/system/
sudo cp deploy/mnq-watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload

# 8. Enable services (don't start yet — need .env configured first)
sudo systemctl enable mnq-watchdog.service    # Watchdog: always enabled
# Don't enable bot and dispatcher until .env is configured and backtest is validated

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Three processes will run on this VPS:"
echo ""
echo "  mnq-bot.service        — The trading bot (vanilla Python + ib_insync)"
echo "  mnq-watchdog.service   — Safety: flattens positions at 15:45 ET (independent)"
echo "  mnq-dispatcher.service — Analysis: auto-debug, recalibrate, consensus eval (ruflo)"
echo ""
echo "Next steps:"
echo "  1. Edit /opt/mnq-orb-bot/.env with your API keys"
echo "  2. Run backtest validation: cd /opt/mnq-orb-bot && .venv/bin/python -m backtest.backtester data/mnq_15m.parquet"
echo "  3. Start paper trading: sudo systemctl start mnq-watchdog mnq-bot"
echo "  4. Start analysis dispatch: sudo systemctl start mnq-dispatcher"
echo ""
echo "The watchdog runs independently. Even if the bot crashes, positions flatten at 15:45 ET."
echo "The dispatcher is non-critical. If it crashes, auto-analysis pauses but trading continues."

"""Hygiene tests — assert on Claude-facing configuration so it can't silently drift.

These tests do not exercise the bot. They guard CLAUDE.md, settings.json, hook
scripts, and slash command files against the kind of regression that turned
`mnq-orb-bot/CLAUDE.md` into a ruflo-themed document after ruflo was dropped
from the plan (commit b7da8e5).
"""

# last30days cron briefing - operational notes

## 2026-06-16 run (first production run)

**Engine state**: degraded. Reddit 403 (RSS fallback only), Polymarket 0 on broad topic, GitHub 0 (no GITHUB_TOKEN), YouTube 1 video / 0 transcripts (quick mode), HN fine.

**Fixes to apply**:
- `gh auth login` (already installed at /opt/homebrew/bin/gh, not authenticated) OR export GITHUB_TOKEN to unlock GitHub source in engine
- Get XAI_API_KEY for X source
- For Polymarket, always pass `--polymarket-keywords` with at least: `AI,stock,market,trading,fed,recession,equity,rate,quant,earnings,ipo` - otherwise returns 0 markets on broad topics
- Reddit 403 is IP-level, `--auto-resolve` does NOT bypass - needs cookie or VPN
- For YouTube transcripts in `--quick`, transcripts = 0 by default - use default depth (no --quick) for transcript capture

**Effective workaround used today**: direct curl to GitHub public search API + engine re-run with narrow Polymarket keywords.

## Main signal clusters - subject: AI quant / ML alpha / algo trading
1. Lewis Jackson Claude Code quant video (263K views) - LLM-as-coder-for-alpha narrative strong
2. r/wallstreetbets AI bubble satire (23,554 upvotes) - retail sentiment peak signal
3. Apple + Gemini architecture announcement (HN 737pts) - AI infra spillover
4. GitHub: AlphaPurify (273 star), vibe-investing (203 star) - LLM-for-alpha tooling booming
5. Polymarket: Nasdaq 24h trading 0.8% - confirms A股 trading hours stay

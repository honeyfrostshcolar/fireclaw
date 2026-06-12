# FireClaw TOML Config File Support — 2026-06-12

## Task Goal

Add TOML config file support so users don't have to pass every setting via CLI flags.

## What Was Done

- Created `src/fireclaw_core/gateway/config.py` — loads `fireclaw.toml`, merges with CLI args (CLI wins)
- Updated `mission_cli.py` serve handler to load config file and merge
- Added `--config` flag to serve subcommand
- Created `fireclaw.example.toml` template
- Added `fireclaw.toml` to `.gitignore` (may contain API keys)
- Commit: `7594fd0`

## Config File Structure

```toml
[server]
host = "0.0.0.0"
port = 8766
data_dir = "./data"

[planner]
type = "llm"

[provider]
base_url = "https://api.deepseek.com/v1"
api_key = "sk-..."
model = "deepseek-chat"

[robot_agent]
enabled = true
planner = "llm"

[robot_agent.provider]  # optional, falls back to [provider]
base_url = "..."
api_key = "..."
model = "..."
```

## Usage

```bash
cp fireclaw.example.toml fireclaw.toml
# edit fireclaw.toml with your API credentials
python -m fireclaw_core serve
# CLI flags override config: python -m fireclaw_core serve --model other-model
```

## Why

CLI-only configuration is impractical for production use. Users need to persist API keys, model choices, and server settings without exposing them in shell history or scripts.

## How to Apply

When adding new configurable settings: add to `config.py` load_config(), add to `fireclaw.example.toml`, add CLI flag with `default=None`, and wire through merge_config().

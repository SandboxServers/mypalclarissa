# MyPalClarissa

Personal AI assistant with persistent memory, multi-platform support, and proactive monitoring.

## Features

### Core
- **Persistent Memory** - Remembers facts and preferences across sessions via [mem0](https://github.com/mem0ai/mem0)
- **Multi-Platform** - Discord bot, REST API, and Next.js web interface
- **Session Management** - Threaded conversations with context carryover
- **Multiple LLM Backends** - OpenRouter, Anthropic, NanoGPT, OpenAI-compatible

### Smart Model Selection
Inspired by [KIRA](https://github.com/krafton-ai/KIRA), Clarissa automatically selects the optimal model tier based on task complexity:

| Tier | Model Class | Used For |
|------|-------------|----------|
| High | Opus | Code review, complex reasoning, creative writing |
| Mid | Sonnet | General conversation, moderate tasks (default) |
| Low | Haiku | Simple queries, quick responses |

Manual overrides via message prefix: `!high`, `!mid`, `!low`

### Multi-User Group Chat
Inspired by [HuixiangDou](https://github.com/InternLM/HuixiangDou), Clarissa handles group conversations intelligently:

- **Organic Responses** - Responds without @ mentions when contextually appropriate
- **Rejection Classifier** - Filters ambient chatter, only engages when relevant
- **Per-Channel Tuning** - Adjust sensitivity with `/sensitivity` command
- **Participant Tracking** - Maintains context across multiple users
- **Coreference Resolution** - Resolves pronouns to recent entities

### Tool Calling
- **Docker Sandbox** - Safe code execution in isolated containers
- **Web Search** - Tavily-powered internet search
- **File Management** - Local file storage that persists across sessions
- **Browser Automation** - Playwright for screenshots and page scraping (optional)

### Integrations
- **GitHub** - Issues, PRs, commits, workflows, gists, notifications
- **Azure DevOps** - Work items, repos, pipelines, wikis
- **Email** - Monitoring and auto-response

### Proactive Monitoring
Background checkers that notify you of important updates:

| Checker | Monitors |
|---------|----------|
| GitHub | PR reviews requested, CI failures, mentions |
| Azure DevOps | Work items assigned, PR reviews, pipeline failures |
| Email | New emails matching criteria |

## Installation

```bash
poetry install
```

## Quick Start

### Discord Bot
```bash
poetry run python discord_bot.py
```

### API Server
```bash
poetry run python api.py
```

### Web Frontend
```bash
cd frontend
npm install
npm run dev
```

### Docker Compose
```bash
# Full stack (API + frontend)
docker-compose --profile full up

# Discord bot only
docker-compose --profile discord up

# With PostgreSQL databases
docker-compose --profile discord --profile postgres up
```

## Configuration

Copy `.env.example` to `.env` and configure:

### Required
```bash
OPENAI_API_KEY=...              # For mem0 embeddings
DISCORD_BOT_TOKEN=...           # For Discord bot
```

### LLM Provider (choose one)
```bash
LLM_PROVIDER=openrouter         # or: anthropic, nanogpt, openai
OPENROUTER_API_KEY=...          # If using OpenRouter
ANTHROPIC_API_KEY=...           # If using Anthropic
```

### Auto Tier Selection
```bash
AUTO_TIER_ENABLED=true          # Enable automatic model selection
AUTO_TIER_SHOW_SELECTION=false  # Show selected tier to user
```

### Multi-User Handling
```bash
ORGANIC_RESPONSE_ENABLED=true   # Respond without @ mentions
ORGANIC_CONFIDENCE_THRESHOLD=0.4  # Min confidence to respond (0.0-1.0)
ORGANIC_COOLDOWN_MINUTES=3      # Cooldown between organic responses
ORGANIC_DAILY_LIMIT=50          # Max organic responses per day
```

### Proactive Monitoring
```bash
PROACTIVE_ENABLED=true          # Enable background checkers
PROACTIVE_QUIET_START=22        # Quiet hours start (10pm)
PROACTIVE_QUIET_END=8           # Quiet hours end (8am)

GITHUB_CHECKER_ENABLED=true
GITHUB_CHECKER_INTERVAL=15      # Minutes
GITHUB_TOKEN=ghp_...            # Required for GitHub checker
```

### Integrations
```bash
# GitHub
GITHUB_TOKEN=ghp_...

# Azure DevOps
AZURE_DEVOPS_ORG=your-org
AZURE_DEVOPS_PAT=...

# Web Search
TAVILY_API_KEY=...
```

### Database (Production)
```bash
DATABASE_URL=postgresql://...       # Main database
MEM0_DATABASE_URL=postgresql://...  # Vector store (pgvector)
```

## Discord Commands

### Slash Commands
| Command | Description |
|---------|-------------|
| `/sensitivity <value>` | Set channel response threshold (0.1-0.6) |
| `/quiet [on\|off]` | Toggle quiet mode (only respond to mentions) |
| `/stats` | Show channel response statistics |

### Message Prefixes
| Prefix | Effect |
|--------|--------|
| `!high` or `!opus` | Force high-tier model |
| `!mid` or `!sonnet` | Force mid-tier model |
| `!low` or `!haiku` | Force low-tier model |

## Memory System

MyPalClarissa uses [mem0](https://github.com/mem0ai/mem0) for persistent memory:

- **Vector Store** - Semantic search over memories (Qdrant or pgvector)
- **Graph Store** - Relationship tracking (Neo4j or Kuzu, optional)

### Bootstrap Profile
```bash
# Dry run - generate JSON
poetry run python -m src.bootstrap_memory

# Apply to mem0
poetry run python -m src.bootstrap_memory --apply
```

### Clear Memory
```bash
poetry run python clear_dbs.py           # With confirmation
poetry run python clear_dbs.py --yes     # Skip confirmation
poetry run python clear_dbs.py --user X  # Specific user
```

## Architecture

```
├── api.py                 # FastAPI server
├── discord_bot.py         # Discord bot with tool calling
├── clarissa_core/
│   ├── llm.py            # LLM provider abstraction
│   ├── memory.py         # Memory manager (mem0 integration)
│   ├── pipeline.py       # Message processing pipeline
│   ├── intent.py         # Intent detection
│   ├── tier_selector.py  # Auto model tier selection
│   ├── rejection.py      # Group chat response filtering
│   └── group_session.py  # Multi-user session tracking
├── tools/                 # Tool definitions
│   ├── github/           # GitHub integration
│   ├── ado/              # Azure DevOps integration
│   └── ...
├── checkers/             # Proactive monitoring
│   ├── github.py
│   ├── ado.py
│   └── email.py
├── frontend/             # Next.js web UI
└── db/                   # Database models
```

## License

MIT

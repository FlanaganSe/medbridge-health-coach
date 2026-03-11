---
description: Technology choices and constraints.
---
# Stack

- **Runtime**: Python 3.12+
- **Framework**: LangGraph (LangChain ecosystem)
- **Frontend**: N/A (backend AI service — patient UI is MedBridge Go; internal demo UI in `demo-ui/` uses React + Vite, dev/staging only)
- **Database**: PostgreSQL (production), SQLite (local dev)
- **Styling**: N/A
- **Tests**: pytest
- **Package manager**: uv
- **Linter**: Ruff (lint + format)
- **Type checker**: pyright

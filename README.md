# .NET Migration Agent

An AI-powered agentic tool that automatically migrates legacy .NET Framework applications to modern .NET 8. Upload your project, click Run Migration, and download the migrated code.

**Live Demo:** [https://dotnet-migration-agent.onrender.com](https://dotnet-migration-agent.onrender.com)

---

## What It Does

The agent runs a 5-step pipeline on your uploaded project:

| Step | Agent | What it does |
|------|-------|-------------|
| 1 | **Ingestion Agent** | Extracts your upload into an isolated workspace |
| 2 | **LLM Migration Agent** | Rewrites every `.cs` and `.csproj` file to .NET 8 / C# 12 using Groq AI |
| 3 | **Auth Agent** | Detects JWT, Identity, Cookie, Forms auth — injects correct .NET 8 auth templates deterministically |
| 4 | **Fix Agent** | Removes legacy usings, fixes DbContext, cleans packages — no LLM |
| 5 | **Build Validator** | Pre-cleans legacy leftovers, runs `dotnet build`, auto-fixes errors, retries up to 3 times |

After the pipeline completes you get:

- Migrated project as a downloadable `.zip`
- Readiness Scorecard with a real score based on build status and remaining issues
- Auth Migration Report — what auth was detected, what was changed, what needs manual setup
- Manual Fix List — structural leftovers and code issues still requiring attention
- Migration Diff, Dependency Map, Code Rewrite Preview, Build Error AI Fixer, and more

---

## Supported Migrations

| From | To |
|------|----|
| .NET Framework 4.5 | .NET 8 |
| .NET Framework 4.6 | .NET 8 |
| .NET Framework 4.7 | .NET 8 |
| .NET Framework 4.8 | .NET 8 |
| .NET Core 3.1 | .NET 8 |
| .NET 5 / 6 / 7 | .NET 8 |

---

## Tech Stack

- **Frontend:** React 19 + Vite 7
- **Backend:** Python 3.11 + FastAPI
- **LLM:** Groq (llama-3.3-70b-versatile)
- **Build validation:** .NET 8 SDK CLI (`dotnet restore`, `dotnet build`)
- **Deployment:** Docker (single container — frontend served by FastAPI)

---

## Project Structure

```
.NetMigrationAgent/
├── frontend/                  # React + Vite frontend
│   ├── src/
│   │   ├── main.jsx           # All UI components
│   │   └── styles.css
│   ├── vite.config.js
│   └── package.json
├── MigrationAgent.API/        # FastAPI backend
│   ├── agents/
│   │   ├── analyzer.py        # Project scanning and complexity scoring
│   │   ├── migrator.py        # LLM-based file rewriting
│   │   ├── auth_agent.py      # Auth detection, template injection, verification
│   │   ├── fixer.py           # Deterministic structural fixes
│   │   ├── build_validator.py # Pre-clean + dotnet build loop + auto-fix
│   │   ├── reporter.py        # Report generation
│   │   └── llm.py             # Groq API client
│   ├── routers/
│   │   ├── files.py           # Upload, GitHub fetch, download
│   │   ├── migration.py       # Migration pipeline + runtime routes
│   │   └── ollama_router.py   # Backend status check
│   ├── main.py                # FastAPI app entry point
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── Dockerfile                 # Root multi-stage build (Node + Python + .NET)
├── render.yaml                # Render deployment config
└── README.md
```

---

## Run Locally

### Prerequisites

- Python 3.11+
- Node.js 20+
- .NET 8 SDK (optional — needed for build validation)
- Groq API key — get one free at [console.groq.com](https://console.groq.com)

### 1. Configure environment

```powershell
cd MigrationAgent.API
copy .env.example .env
```

Edit `.env` and add your Groq API keys:

```env
GROQ_API_KEY_1=gsk_your_key_here
GROQ_API_KEY_2=gsk_your_second_key_here   # optional, used for rate limit rotation
```

### 2. Install backend dependencies

```powershell
cd MigrationAgent.API
pip install -r requirements.txt
```

### 3. Build the frontend

```powershell
cd frontend
npm install
npm run build
```

This outputs the React build into `MigrationAgent.API/frontend/dist` where FastAPI serves it.

### 4. Start the server

```powershell
cd MigrationAgent.API
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Open **http://127.0.0.1:8000**

---

## How to Use

1. **Upload** — Drop a `.zip` of your legacy .NET project or paste a public GitHub URL
2. **Analyze** — The agent scans your project and shows complexity, findings, and packages
3. **Run Migration** — Click Run Migration and watch the 5 agents work in real time
4. **Review** — Check the Readiness Scorecard, Auth Migration Report, and Manual Fix List
5. **Download** — Download the migrated `.zip` and open it in Visual Studio or VS Code

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/files/upload` | Upload project files or zip |
| POST | `/api/files/upload-github` | Fetch from public GitHub URL |
| GET | `/api/files/download` | Download migrated project zip |
| POST | `/api/migration/analyze` | Analyze uploaded project |
| POST | `/api/migration/migrate` | Start migration job |
| GET | `/api/migration/status/{job_id}` | Poll migration progress |
| GET | `/api/migration/report` | Get full migration report |
| GET | `/api/ollama/status` | Check LLM backend status |
| GET | `/health` | Health check |

---

## Deploy on Render

### One-click deploy

1. Fork or push this repo to GitHub
2. Go to [render.com](https://render.com) → **New +** → **Web Service**
3. Connect your GitHub repo
4. Set these settings:

| Setting | Value |
|---------|-------|
| Runtime | Docker |
| Dockerfile Path | `./Dockerfile` |
| Docker Context | `.` |
| Health Check Path | `/health` |

5. Add environment variables:

| Key | Value |
|-----|-------|
| `GROQ_API_KEY_1` | your Groq API key |
| `GROQ_API_KEY_2` | second key (optional) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` |

6. Click **Create Web Service** — first build takes ~5–8 minutes

### Notes

- Free tier spins down after 15 min inactivity — first request after sleep takes ~30 seconds
- Uploaded files and migration outputs are ephemeral — cleared on every server restart
- Build validation requires .NET 8 SDK — included in the Docker image

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY_1` | Yes | Primary Groq API key |
| `GROQ_API_KEY_2` | No | Secondary key for rate limit rotation |
| `GROQ_MODEL` | No | Groq model name (default: `llama-3.3-70b-versatile`) |

---

## What the Agent Cannot Do

- Fix business logic errors introduced by the LLM
- Configure external services (database, Redis, etc.) — these need manual setup
- Migrate Windows Authentication — environment specific, flagged for manual review
- Migrate custom auth middleware — flagged for manual review
- Guarantee zero build errors on every project — complex projects may need manual fixes after migration

---

## Known Limitations

- Migration speed depends on Groq rate limits — a 10-file project takes ~2–3 minutes
- Large projects (50+ files) may hit rate limits and require multiple retries
- Razor views (`.cshtml`) are migrated but may need manual review for complex tag helpers
- Projects with heavy reflection or dynamic code generation may not migrate cleanly

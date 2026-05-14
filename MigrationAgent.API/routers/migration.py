from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from agents.analyzer import analyze
from agents.migrator import migrate
from agents.fixer import run_fixes
from agents.auth_agent import run_auth_agent
from agents.view_migrator import run_view_migrator
from agents.webforms_migrator import run_webforms_migrator
from agents.blazor_migrator import run_blazor_migrator
from agents.build_validator import run_build_validator
from agents.validator import validate
from agents.reporter import generate_report
import uuid
import socket
import subprocess
import threading
import time
import urllib.request
import urllib.error
from typing import Dict, Any
import os
import shutil
from pathlib import Path

router = APIRouter(prefix="/api/migration", tags=["migration"])

BASE_DIR = Path(__file__).parent.parent
UPLOAD_DIR = str(BASE_DIR / "uploads")
OUTPUT_DIR = str(BASE_DIR / "outputs" / "migrated")

migration_jobs: Dict[str, Dict[str, Any]] = {}
runtime_apps:   Dict[str, Dict[str, Any]] = {}

class MigrationRequest(BaseModel):
    from_version: str
    to_version: str

class MigrateRequest(BaseModel):
    from_version: str
    to_version: str

def run_migration_job(job_id: str, upload_dir: str, from_version: str, to_version: str):
    try:
        migration_jobs[job_id]["status"] = "running"
        migration_jobs[job_id]["stage"] = "migrating"
        migration_jobs[job_id]["progress"] = "Starting migration..."

        def update_progress(message: str):
            migration_jobs[job_id]["progress"] = message

        # Clear previous output before starting fresh
        output_path = Path(OUTPUT_DIR)
        if output_path.exists():
            shutil.rmtree(output_path)
        output_path.mkdir(parents=True, exist_ok=True)
        # Clear previous zip if exists
        old_zip = Path(OUTPUT_DIR).parent / "migrated_project.zip"
        if old_zip.exists():
            old_zip.unlink()

        # Step 1 — LLM Migration
        result = migrate(upload_dir, from_version, to_version, progress_callback=update_progress)

        if not result["success"]:
            migration_jobs[job_id]["status"] = "failed"
            migration_jobs[job_id]["stage"] = "failed"
            migration_jobs[job_id]["error"] = result.get("error", "Unknown error")
            migration_jobs[job_id]["progress"] = "Migration failed."
            return

        # Step 2 — Auth Agent (deterministic, no LLM)
        update_progress("Auth Agent: Detecting and migrating authentication...")
        auth_result = {}
        try:
            auth_result = run_auth_agent(
                upload_dir=upload_dir,
                output_dir=OUTPUT_DIR,
                progress_callback=update_progress
            )
        except Exception as ae:
            update_progress(f"Auth Agent warning: {str(ae)}")

        # Step 3 — View Migration Agent (only runs if .cshtml files exist)
        update_progress("View Migration Agent: Checking for Razor views...")
        view_result = {}
        try:
            view_result = run_view_migrator(
                output_dir=OUTPUT_DIR,
                from_version=from_version,
                to_version=to_version,
                progress_callback=update_progress
            )
        except Exception as ve:
            update_progress(f"View Migration Agent warning: {str(ve)}")

        # Step 4 — Web Forms Migration Agent (only runs if .aspx files exist)
        update_progress("Web Forms Agent: Checking for Web Forms files...")
        webforms_result = {}
        try:
            webforms_result = run_webforms_migrator(
                output_dir=OUTPUT_DIR,
                from_version=from_version,
                to_version=to_version,
                progress_callback=update_progress
            )
        except Exception as we:
            update_progress(f"Web Forms Agent warning: {str(we)}")

        # Step 5 — Blazor Migration Agent (only runs if .razor files exist)
        update_progress("Blazor Agent: Checking for Blazor components...")
        blazor_result = {}
        try:
            blazor_result = run_blazor_migrator(
                output_dir=OUTPUT_DIR,
                from_version=from_version,
                to_version=to_version,
                progress_callback=update_progress
            )
        except Exception as be:
            update_progress(f"Blazor Agent warning: {str(be)}")

        # Step 6 — Fix Agent (deterministic fixes, no LLM)
        update_progress("Fix Agent: Applying structural fixes...")
        manual_fixes = []
        try:
            fix_result = run_fixes(
                output_dir=OUTPUT_DIR,
                upload_dir=upload_dir,
                progress_callback=update_progress
            )
            fix_count = fix_result.get("count", 0)
            manual_fixes = fix_result.get("manual_fixes", [])
            update_progress(f"Fix Agent: {fix_count} fixes applied successfully.")
        except Exception as fe:
            fix_count = 0
            update_progress(f"Fix Agent warning: {str(fe)}")

        # Step 6 — Build Validator (pre-clean + build loop + auto-fix)
        update_progress("Build Validator: Starting pre-build cleanup and validation...")
        build_result = {}
        try:
            build_result = run_build_validator(
                output_dir=OUTPUT_DIR,
                progress_callback=update_progress
            )
        except Exception as bve:
            update_progress(f"Build Validator warning: {str(bve)}")

        # Step 7 — done
        migration_jobs[job_id]["status"] = "completed"
        migration_jobs[job_id]["stage"] = "completed"
        # Filter out the [merged into Program.cs] placeholder from migrated dict
        result["migrated"] = {k: v for k, v in result.get("migrated", {}).items() if v != "[merged into Program.cs]"}
        migration_jobs[job_id]["result"] = result
        migration_jobs[job_id]["result"]["manual_fixes"] = manual_fixes
        migration_jobs[job_id]["result"]["auth"] = auth_result
        migration_jobs[job_id]["result"]["view_migration"] = view_result
        migration_jobs[job_id]["result"]["webforms_migration"] = webforms_result
        migration_jobs[job_id]["result"]["blazor_migration"] = blazor_result
        migration_jobs[job_id]["result"]["build_validation"] = build_result
        build_passed = build_result.get("success", False)
        migration_jobs[job_id]["progress"] = (
            f"Migration completed. {result['count']} files migrated. "
            f"{fix_count} fixes applied. "
            f"Build {'passed' if build_passed else 'needs review'}."
        )

    except Exception as e:
        migration_jobs[job_id]["status"] = "failed"
        migration_jobs[job_id]["error"] = str(e)
        migration_jobs[job_id]["progress"] = f"Migration failed: {str(e)}"

@router.post("/analyze")
def run_analysis(request: MigrationRequest):
    return analyze(
        upload_dir=UPLOAD_DIR,
        from_version=request.from_version,
        to_version=request.to_version
    )

@router.post("/migrate")
def run_migration(request: MigrateRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    migration_jobs[job_id] = {
        "status": "queued",
        "stage": "queued",
        "step": 0,
        "progress": "Migration queued...",
        "created_at": time.time()
    }
    background_tasks.add_task(run_migration_job, job_id, UPLOAD_DIR, request.from_version, request.to_version)
    return {"job_id": job_id, "status": "queued", "message": "Migration started in background"}

@router.get("/status/{job_id}")
def get_migration_status(job_id: str):
    if job_id not in migration_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = migration_jobs[job_id]
    return {
        "job_id": job_id,
        "status": job["status"],
        "stage": job.get("stage", "unknown"),
        "step": job.get("step", 0),
        "progress": job.get("progress", ""),
        "result": job.get("result"),
        "error": job.get("error"),
        "created_at": job.get("created_at")
    }

@router.post("/validate")
def run_validation():
    return validate(output_dir=OUTPUT_DIR, progress_callback=None)

@router.get("/report")
def get_report():
    return generate_report()


# ── Runtime routes ────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def _is_port_open(port: int) -> bool:
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.3):
            return True
    except OSError:
        return False

def _find_csproj(output_dir: str):
    out = Path(output_dir)
    projects = [f for f in out.rglob('*.csproj')
                if not any(p.lower() in {'obj','bin'} for p in f.parts)]
    if not projects:
        return None
    web = [p for p in projects if 'Microsoft.NET.Sdk.Web' in
           p.read_text(encoding='utf-8', errors='ignore')]
    return web[0] if web else projects[0]

def _capture_logs(job_id: str, process: subprocess.Popen):
    app = runtime_apps[job_id]
    try:
        for line in process.stdout:
            app['logs'].append(line.rstrip())
            if len(app['logs']) > 500:
                app['logs'] = app['logs'][-500:]
    except Exception:
        pass

def _check_runnable(output_dir: str) -> dict:
    """Check if the migrated app is likely runnable without external dependencies."""
    out = Path(output_dir)
    reasons = []

    for cs_file in out.rglob('*.cs'):
        if any(p.lower() in {'obj', 'bin'} for p in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding='utf-8', errors='ignore')
            if 'Host=' in content and 'Database=' in content:
                reasons.append('Requires PostgreSQL database — configure connection string in appsettings.json before running.')
            if 'Server=' in content and ('Initial Catalog=' in content or 'Database=' in content):
                reasons.append('Requires SQL Server database — configure connection string in appsettings.json before running.')
            if 'mongodb://' in content.lower() or 'MongoClient' in content:
                reasons.append('Requires MongoDB — configure connection string before running.')
            if 'redis' in content.lower() and 'ConnectionMultiplexer' in content:
                reasons.append('Requires Redis — configure connection before running.')
        except Exception:
            pass

    for json_file in out.rglob('appsettings*.json'):
        try:
            content = json_file.read_text(encoding='utf-8', errors='ignore')
            if 'Host=' in content and 'Database=' in content:
                reasons.append('PostgreSQL connection string found in appsettings.json — ensure the database is running locally.')
            if 'Server=' in content and 'Database=' in content:
                reasons.append('SQL Server connection string found in appsettings.json — ensure the database is running locally.')
        except Exception:
            pass

    # Deduplicate
    reasons = list(dict.fromkeys(reasons))
    return {'runnable': len(reasons) == 0, 'reasons': reasons}


@router.post("/run/{job_id}")
def start_runtime(job_id: str):
    if job_id not in migration_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if migration_jobs[job_id].get('status') != 'completed':
        raise HTTPException(status_code=400, detail="Migration not completed yet")

    # Kill any existing process for this job and clean obj/bin locks
    existing = runtime_apps.get(job_id)
    if existing:
        proc = existing.get('process')
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        # Delete obj/bin to release file locks before next run
        out = Path(OUTPUT_DIR)
        for folder in out.rglob('obj'):
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
        for folder in out.rglob('bin'):
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)

    if not shutil.which('dotnet'):
        return {'status': 'failed', 'url': '', 'logs': ['.NET SDK not found — install dotnet to run the migrated app.']}

    csproj = _find_csproj(OUTPUT_DIR)
    if not csproj:
        return {'status': 'failed', 'url': '', 'logs': ['No runnable .csproj found in migrated output.']}

    # Warn upfront if external dependencies are detected
    runnable = _check_runnable(OUTPUT_DIR)
    if not runnable['runnable']:
        return {
            'status': 'needs_setup',
            'url': '',
            'logs': [
                'This app requires external services to run:',
                *[f'  - {r}' for r in runnable['reasons']],
                '',
                'To run locally:',
                '  1. Download the migrated zip',
                '  2. Set up the required services',
                '  3. Update appsettings.json with your connection details',
                '  4. Run: dotnet run',
            ]
        }

    port = _free_port()
    url = f'http://0.0.0.0:{port}'

    # Clean stale obj/bin before running
    for folder in ['obj', 'bin']:
        stale = csproj.parent / folder
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)

    process = subprocess.Popen(
        ['dotnet', 'run', '--project', str(csproj), '--urls', url, '--no-launch-profile'],
        cwd=str(csproj.parent),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
        env={**os.environ, 'ASPNETCORE_ENVIRONMENT': 'Development'},
    )
    display_url = f'http://127.0.0.1:{port}'
    runtime_apps[job_id] = {
        'status': 'starting', 'url': display_url, 'port': port,
        'process': process, 'logs': [f'Starting {csproj.name} on {display_url}'],
    }
    threading.Thread(target=_capture_logs, args=(job_id, process), daemon=True).start()
    for _ in range(30):
        if process.poll() is not None:
            runtime_apps[job_id]['status'] = 'failed'
            break
        if _is_port_open(port):
            runtime_apps[job_id]['status'] = 'running'
            break
        time.sleep(0.5)
    return _runtime_status(job_id)

@router.get("/run/{job_id}")
def get_runtime(job_id: str):
    return _runtime_status(job_id)

@router.post("/run/{job_id}/stop")
def stop_runtime(job_id: str):
    app = runtime_apps.get(job_id)
    if not app:
        return {'status': 'stopped', 'url': '', 'logs': []}
    proc = app.get('process')
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    app['status'] = 'stopped'
    app['logs'].append('Application stopped.')
    return _runtime_status(job_id)

def _detect_routes(output_dir: str) -> list:
    """Scan migrated controllers and Razor Pages to extract real routes."""
    routes = []
    out = Path(output_dir)

    # Scan controllers
    for cs_file in out.rglob('*Controller.cs'):
        if any(p.lower() in {'obj', 'bin'} for p in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding='utf-8', errors='ignore')
            ctrl_name = cs_file.stem.replace('Controller', '').lower()
            for match in re.findall(r'\[Route\(["\']([^"\']+)["\']\)\]', content):
                route = match.replace('[controller]', ctrl_name)
                if not route.startswith('/'):
                    route = '/' + route
                if 'api' in route.lower() and route not in routes:
                    routes.append(route)
        except Exception:
            pass

    # Scan Razor Pages — add page routes
    razor_pages = [
        f for f in out.rglob('*.cshtml')
        if not any(p.lower() in {'obj', 'bin'} for p in f.parts)
        and not f.name.startswith('_')
    ]
    if razor_pages:
        # Always add root for Razor Pages projects
        if '/' not in routes:
            routes.insert(0, '/')
        for page in razor_pages[:4]:
            # Convert Pages/Info.cshtml -> /Info
            try:
                parts = list(page.parts)
                pages_idx = next((i for i, p in enumerate(parts) if p.lower() == 'pages'), None)
                if pages_idx is not None:
                    rel_parts = parts[pages_idx + 1:]
                    route = '/' + '/'.join(p.replace('.cshtml', '') for p in rel_parts)
                    if route not in routes and 'shared' not in route.lower():
                        routes.append(route)
            except Exception:
                pass

    # Always add /health as optional
    if '/health' not in routes:
        routes.append('/health')

    # Fallback for pure API projects
    if not routes or routes == ['/health']:
        routes = ['/api', '/health']

    return routes[:6]


@router.post("/run/{job_id}/smoke")
def smoke_test(job_id: str):
    # Start app if not running
    app = runtime_apps.get(job_id)
    if not app or app.get('status') != 'running':
        start_runtime(job_id)
        app = runtime_apps.get(job_id)
    if not app or app.get('status') != 'running':
        return {'status': 'failed', 'summary': 'App did not start.', 'checks': [], 'runtime': _runtime_status(job_id)}

    base_url = app['url'].rstrip('/')
    routes = _detect_routes(OUTPUT_DIR)
    checks = []
    for path in routes:
        name = path.strip('/').replace('/', ' › ') or 'root'
        url = f'{base_url}{path}'
        optional = path == '/health'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'MigrationAgentSmokeTest/1.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                status_code = resp.status
                passed = 200 <= status_code < 400
        except urllib.error.HTTPError as e:
            status_code = e.code
            passed = optional  # health 404 is acceptable
        except Exception:
            status_code = 0
            passed = False
        checks.append({'name': name, 'path': path, 'status_code': status_code, 'passed': passed, 'optional': optional})

    passed_count = sum(1 for c in checks if c['passed'])
    required = [c for c in checks if not c.get('optional')]
    required_passed = sum(1 for c in required if c['passed'])
    overall = 'passed' if required_passed == len(required) else 'needs_review'
    return {
        'status': overall,
        'summary': f'{passed_count}/{len(checks)} checks passed ({required_passed}/{len(required)} required).',
        'url': base_url,
        'checks': checks,
        'runtime': _runtime_status(job_id),
    }

def _runtime_status(job_id: str) -> dict:
    app = runtime_apps.get(job_id)
    if not app:
        return {'status': 'stopped', 'url': '', 'logs': []}
    proc = app.get('process')
    if proc:
        if proc.poll() is not None and app['status'] not in {'stopped', 'failed'}:
            app['status'] = 'exited'
        elif proc.poll() is None and _is_port_open(app.get('port', 0)):
            app['status'] = 'running'
    return {'status': app['status'], 'url': app.get('url', ''), 'logs': app.get('logs', [])[-200:]}

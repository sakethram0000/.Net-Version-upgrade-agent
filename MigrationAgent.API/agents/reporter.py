from pathlib import Path
import re
from agents.auth_agent import run_auth_agent

BASE_DIR = Path(__file__).parent.parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "outputs" / "migrated"
UPLOAD_DIR = BASE_DIR / "uploads"


def generate_report():
    migrated_dir = DEFAULT_OUTPUT_DIR
    upload_dir = UPLOAD_DIR

    empty = {
        "summary": "",
        "from_version": "",
        "to_version": "",
        "changes": [],
        "issues": [],
        "recommendations": [],
        "dependency_map": {},
        "manual_fixes": [],
        "readiness": {"score": 0, "level": "Unknown", "summary": "", "categories": [], "recommendations": []},
        "auth_migration": {},
        "validation": {"success": False, "stage": "not run", "output": "", "errors": ""},
        "diff": {"summary": {"added": 0, "modified": 0, "removed": 0, "unchanged": 0}, "added": [], "modified": [], "removed": [], "previews": []},
        "code_rewrite_previews": [],
        "build_fixer": {"summary": "", "items": []},
        "dependency_modernization": {"summary": "", "items": []},
        "architecture_suggestions": {"summary": "", "items": []},
        "generated_tests": {"summary": "", "items": []},
        "executive_report": {},
    }

    if not migrated_dir.exists():
        empty["issues"].append("No migrated output found.")
        empty["recommendations"].append("Run migration first.")
        return empty

    migrated_files = (
        list(migrated_dir.rglob("*.cs"))
        + list(migrated_dir.rglob("*.csproj"))
        + list(migrated_dir.rglob("*.sln"))
    )

    # --- changes ---
    changes = []
    for f in migrated_files:
        rel = str(f.relative_to(migrated_dir))
        if f.suffix == ".cs":
            changes.append({"file": rel, "summary": "Migrated to .NET 8 / C# 12"})
        elif f.suffix == ".csproj":
            changes.append({"file": rel, "summary": "Updated to .NET 8 SDK-style project"})
        elif f.suffix == ".sln":
            changes.append({"file": rel, "summary": "Solution file preserved"})

    # --- dependency_map ---
    dependency_map = {}
    for csproj in migrated_dir.rglob("*.csproj"):
        try:
            content = csproj.read_text(encoding="utf-8", errors="ignore")
            for pkg, ver in re.findall(r'<PackageReference Include="([^"]+)" Version="([^"]+)"', content):
                dependency_map[pkg] = ver
        except Exception:
            pass

    # --- manual_fixes ---
    manual_fixes = []
    # Scan migrated output for remaining code issues
    for cs_file in migrated_dir.rglob("*.cs"):
        if any(p.lower() in {"obj", "bin"} for p in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            rel = str(cs_file.relative_to(migrated_dir))
            if "TODO" in content or "FIXME" in content:
                manual_fixes.append(f"{rel}: Contains TODO/FIXME comments requiring attention")
            if "System.Web" in content:
                manual_fixes.append(f"{rel}: Still contains System.Web references — verify compatibility")
            if re.search(r"async void \w+\(", content):
                manual_fixes.append(f"{rel}: Contains async void methods — consider async Task instead")
            if "HttpContext.Current" in content:
                manual_fixes.append(f"{rel}: Contains HttpContext.Current — replace with IHttpContextAccessor")
            if "ConfigurationManager" in content:
                manual_fixes.append(f"{rel}: Contains ConfigurationManager — replace with IConfiguration")
        except Exception:
            pass
    # Scan for structural leftovers that should have been removed
    structural_leftovers = [
        ("packages.config",  "packages.config still present — migrate to PackageReference in .csproj"),
        ("Web.config",       "Web.config still present — review IIS/system.web settings, not needed in ASP.NET Core"),
        ("Global.asax",      "Global.asax still present — startup hooks should be in Program.cs"),
        ("Global.asax.cs",   "Global.asax.cs still present — merge application startup logic into Program.cs"),
        ("App_Start",        "App_Start folder still present — BundleConfig/RouteConfig/FilterConfig not needed in ASP.NET Core"),
        ("AssemblyInfo.cs",  "AssemblyInfo.cs still present — not needed in SDK-style projects"),
    ]
    for filename, message in structural_leftovers:
        matches = list(migrated_dir.rglob(filename))
        for match in matches:
            if any(p.lower() in {"obj", "bin"} for p in match.parts):
                continue
            rel = str(match.relative_to(migrated_dir))
            manual_fixes.append(f"{rel}: {message}")

    # --- diff (compare upload vs migrated) ---
    diff = _build_diff(upload_dir, migrated_dir)

    # --- code_rewrite_previews ---
    code_rewrite_previews = _build_rewrite_previews(upload_dir, migrated_dir)

    # --- validation (use build_validator result if available, else run fresh) ---
    from agents.build_validator import run_build_validator
    validation_raw = run_build_validator(str(migrated_dir))
    validation = {
        "success":  validation_raw.get("success", False),
        "stage":    "build",
        "output":   validation_raw.get("output", ""),
        "errors":   validation_raw.get("output", "") if not validation_raw.get("success") else "",
        "skipped":  validation_raw.get("skipped", False),
        "reason":   validation_raw.get("reason", ""),
        "auto_fixes": validation_raw.get("auto_fixes", []) + validation_raw.get("pre_clean_fixes", []),
        "error_list": validation_raw.get("errors", []),
    }

    # --- build_fixer ---
    build_fixer = _build_fixer(validation)

    # --- dependency_modernization ---
    dependency_modernization = _dependency_modernization(dependency_map)

    # --- architecture_suggestions ---
    architecture_suggestions = _architecture_suggestions(manual_fixes)

    # --- generated_tests ---
    generated_tests = _generated_tests(migrated_dir)

    # --- auth_migration ---
    auth_migration = {}
    try:
        auth_migration = run_auth_agent(
            upload_dir=str(upload_dir),
            output_dir=str(migrated_dir),
        )
    except Exception:
        auth_migration = {"status": "skipped", "summary": "Auth agent could not run during report generation."}

    # --- readiness scorecard ---
    patterns_found = len([c for c in changes if 'System.Web' in c.get('summary','')])
    high_fixes = len([f for f in manual_fixes if any(k in f for k in ['System.Web','Global.asax','packages.config','HttpContext.Current'])])
    medium_fixes = len(manual_fixes) - high_fixes
    build_passed = validation.get('success', False)

    def _score(val): return max(0, min(100, val))

    readiness_categories = [
        {
            'name': 'Build Status',
            'score': _score(95 if build_passed else 40),
            'status': 'Good' if build_passed else 'Risk',
            'description': 'dotnet build passed' if build_passed else 'Build failed or skipped — review errors'
        },
        {
            'name': 'Legacy Code Removed',
            'score': _score(100 - high_fixes * 15),
            'status': 'Good' if high_fixes == 0 else 'Risk',
            'description': f'{high_fixes} high-priority legacy pattern(s) still present' if high_fixes else 'No critical legacy patterns remaining'
        },
        {
            'name': 'Code Quality',
            'score': _score(100 - medium_fixes * 10),
            'status': 'Good' if medium_fixes == 0 else 'Review',
            'description': f'{medium_fixes} code quality item(s) to review' if medium_fixes else 'No code quality issues detected'
        },
        {
            'name': 'Dependencies',
            'score': _score(90 if dependency_map else 60),
            'status': 'Good' if dependency_map else 'Review',
            'description': f'{len(dependency_map)} package(s) migrated to .NET 8' if dependency_map else 'No packages detected in migrated .csproj'
        },
        {
            'name': 'Files Migrated',
            'score': _score(100 if len(changes) > 0 else 0),
            'status': 'Good' if len(changes) > 0 else 'Risk',
            'description': f'{len(changes)} file(s) successfully migrated'
        },
    ]
    readiness_score = round(sum(c['score'] for c in readiness_categories) / len(readiness_categories))
    readiness_level = 'Ready' if readiness_score >= 80 else 'Moderate' if readiness_score >= 60 else 'High Risk'
    readiness_recs = []
    if not build_passed:
        readiness_recs.append('Fix build errors before deploying — check Build Error AI Fixer for details.')
    if high_fixes > 0:
        readiness_recs.append(f'Address {high_fixes} high-priority item(s) in Manual Fix List before deploying.')
    if medium_fixes > 0:
        readiness_recs.append(f'Review {medium_fixes} code quality item(s) in Manual Fix List.')
    if not readiness_recs:
        readiness_recs.append('Migration looks clean — proceed with smoke testing and regression tests.')

    readiness = {
        'score': readiness_score,
        'level': readiness_level,
        'summary': f'{readiness_level} — {readiness_score}/100 migration readiness score.',
        'categories': readiness_categories,
        'recommendations': readiness_recs,
    }

    summary = f"{len(migrated_files)} file(s) migrated successfully to .NET 8."
    recommendations = [
        "Migration completed. Review code for business logic correctness.",
        "Run dotnet build to verify compilation.",
        "Test all API endpoints and database connections.",
    ]

    executive_report = {
        "title": ".NET Migration Executive Report",
        "total_files_migrated": len(migrated_files),
        "build_status": "Passed" if validation.get("success") else "Needs Review",
        "readiness_score": readiness_score,
        "readiness_level": readiness_level,
        "dependency_count": len(dependency_map),
        "manual_fix_count": len(manual_fixes),
        "diff_summary": diff["summary"],
        "recommendations": readiness_recs,
    }

    return {
        "summary": summary,
        "changes": changes,
        "issues": [],
        "recommendations": recommendations,
        "dependency_map": dependency_map,
        "manual_fixes": manual_fixes,
        "readiness": readiness,
        "auth_migration": auth_migration,
        "validation": validation,
        "diff": diff,
        "code_rewrite_previews": code_rewrite_previews,
        "build_fixer": build_fixer,
        "dependency_modernization": dependency_modernization,
        "architecture_suggestions": architecture_suggestions,
        "generated_tests": generated_tests,
        "executive_report": executive_report,
    }


def _build_diff(upload_dir: Path, migrated_dir: Path) -> dict:
    import difflib

    def collect(root):
        result = {}
        if not root.exists():
            return result
        for f in root.rglob("*"):
            if f.is_file() and not any(p.lower() in {"obj", "bin", ".git", ".vs"} for p in f.parts):
                try:
                    result[f.relative_to(root).as_posix()] = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass
        return result

    src = collect(upload_dir)
    out = collect(migrated_dir)

    added = sorted(set(out) - set(src))
    removed = sorted(set(src) - set(out))
    common = sorted(set(src) & set(out))
    modified, previews = [], []

    for rel in common:
        if src[rel] == out[rel]:
            continue
        modified.append(rel)
        if len(previews) < 8:
            diff_lines = list(difflib.unified_diff(
                src[rel].splitlines(), out[rel].splitlines(),
                fromfile=f"original/{rel}", tofile=f"migrated/{rel}",
                lineterm="", n=3,
            ))
            previews.append({"path": rel, "diff": "\n".join(diff_lines[:120])})

    return {
        "summary": {
            "added": len(added),
            "modified": len(modified),
            "removed": len(removed),
            "unchanged": max(0, len(common) - len(modified)),
        },
        "added": added[:60],
        "modified": modified[:60],
        "removed": removed[:60],
        "previews": previews,
    }


def _build_rewrite_previews(upload_dir: Path, migrated_dir: Path) -> list:
    previews = []
    if not migrated_dir.exists():
        return previews
    for out_file in list(migrated_dir.rglob("*.cs"))[:8]:
        rel = out_file.relative_to(migrated_dir).as_posix()
        src_file = upload_dir / rel
        try:
            migrated = out_file.read_text(encoding="utf-8", errors="ignore")
            legacy = src_file.read_text(encoding="utf-8", errors="ignore") if src_file.exists() else ""
            if legacy.strip() == migrated.strip():
                continue
            previews.append({
                "path": rel,
                "legacy": legacy[:4000] or "New file generated during migration.",
                "proposed": migrated[:4000],
                "explanation": "File was rewritten to target .NET 8 / C# 12 conventions.",
            })
        except Exception:
            pass
    return previews


def _run_validation(migrated_dir: Path) -> dict:
    import shutil, subprocess
    if not shutil.which("dotnet"):
        return {
            "success": False,
            "stage": "skipped",
            "output": "dotnet CLI not found — download the zip and run 'dotnet build' locally.",
            "errors": "",
        }
    csproj_files = [f for f in migrated_dir.rglob("*.csproj")
                    if not any(p.lower() in {"obj", "bin"} for p in f.parts)]
    if not csproj_files:
        return {"success": False, "stage": "skipped", "output": "No .csproj found.", "errors": ""}
    project = csproj_files[0]
    try:
        result = subprocess.run(
            ["dotnet", "build", str(project), "--nologo", "-v", "m"],
            capture_output=True, text=True, timeout=120, cwd=str(project.parent)
        )
        return {
            "success": result.returncode == 0,
            "stage": "build",
            "output": result.stdout,
            "errors": result.stderr if result.returncode != 0 else "",
        }
    except Exception as e:
        return {"success": False, "stage": "build", "output": "", "errors": str(e)}


def _build_fixer(validation: dict) -> dict:
    errors_text = validation.get("errors", "") or validation.get("output", "") or ""
    error_codes = re.findall(r"error\s+([A-Z]+\d+):\s*(.*)", errors_text)
    items = []
    for code, message in error_codes[:8]:
        items.append({
            "error": code,
            "root_cause": message[:200],
            "suggested_fix": _fix_hint(code, message),
        })
    if not items:
        items.append({
            "error": "None" if validation.get("success") else "Unknown",
            "root_cause": "Build passed." if validation.get("success") else "No parseable error codes found.",
            "suggested_fix": "No fixes required." if validation.get("success") else "Review build output manually.",
        })
    return {"summary": f"{len(items)} build issue(s) analysed.", "items": items}


def _fix_hint(code: str, message: str) -> str:
    msg = message.lower()
    if "system.web" in msg:
        return "Replace System.Web with ASP.NET Core equivalents."
    if "configurationmanager" in msg:
        return "Replace ConfigurationManager with IConfiguration."
    if "namespace" in msg or "type or namespace" in msg:
        return "Update using statements and NuGet references."
    if "nullable" in msg:
        return "Initialize nullable properties or mark them nullable."
    return "Apply targeted source/package correction and rerun build."


def _dependency_modernization(dependency_map: dict) -> dict:
    hints = {
        "Newtonsoft.Json": ("13.0.3", "Keep or migrate to System.Text.Json if contracts allow."),
        "EntityFramework": ("Microsoft.EntityFrameworkCore 8.x", "Replace EF6 with EF Core 8."),
        "Microsoft.AspNet.Mvc": ("Microsoft.AspNetCore.Mvc", "Replace MVC5 with ASP.NET Core MVC."),
    }
    items = []
    for pkg, ver in dependency_map.items():
        target, note = hints.get(pkg, (f"{ver} (current)", "Confirm .NET 8 compatibility."))
        items.append({"package": pkg, "current_version": ver, "recommended": target, "note": note})
    if not items:
        items.append({"package": "None detected", "current_version": "", "recommended": "", "note": "No packages found in migrated .csproj files."})
    return {"summary": f"{len(items)} dependency recommendation(s).", "items": items}


def _architecture_suggestions(manual_fixes: list) -> dict:
    fix_text = " ".join(manual_fixes)
    items = [
        {"area": "Configuration", "recommendation": "Move settings to IConfiguration and strongly typed options.", "priority": "High" if "ConfigurationManager" in fix_text else "Medium"},
        {"area": "Hosting", "recommendation": "Use ASP.NET Core minimal hosting in Program.cs.", "priority": "High"},
        {"area": "Dependency Injection", "recommendation": "Register services in DI instead of manual instantiation.", "priority": "Medium"},
        {"area": "API Modernization", "recommendation": "Use ControllerBase, attribute routing, and OpenAPI/Swagger.", "priority": "High" if "System.Web" in fix_text else "Medium"},
        {"area": "Observability", "recommendation": "Add structured logging and health checks.", "priority": "Low"},
    ]
    return {"summary": "Architecture modernization suggestions based on migration findings.", "items": items}


def _generated_tests(migrated_dir: Path) -> dict:
    controllers = [f.relative_to(migrated_dir).as_posix() for f in migrated_dir.rglob("*Controller.cs")]
    items = [
        {"name": "SmokeTest.HomePage_Returns200", "type": "Smoke", "target": "/", "sample": "Assert.True(response.IsSuccessStatusCode);"},
        {"name": "SmokeTest.HealthEndpoint_Returns200", "type": "Smoke", "target": "/health", "sample": "Assert.Equal(HttpStatusCode.OK, response.StatusCode);"},
    ]
    for c in controllers[:6]:
        items.append({"name": f"{Path(c).stem}Tests.Actions_ReturnExpectedResult", "type": "Controller", "target": c, "sample": "Assert.NotNull(result);"})
    return {"summary": f"{len(items)} starter test scenario(s) generated.", "items": items, "suggested_project": "MigratedApp.Tests"}

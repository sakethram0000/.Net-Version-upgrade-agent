"""
Build Validator Agent — runs after Fix Agent.
Layer 1: Pre-build cleanup of known legacy leftovers that cause build failures.
Layer 2: dotnet build → parse errors → auto-fix → retry (up to 3 times).
Layer 3: Returns structured result for report and readiness scorecard.
No LLM involved — fully deterministic.
"""
from pathlib import Path
import re
import shutil
import subprocess

SKIP_FOLDERS = {"obj", "bin", ".vs", ".git", "node_modules"}


# ── Layer 1: Pre-build cleanup ────────────────────────────────────────────

def pre_build_clean(output_dir: str, progress_callback=None) -> list:
    """
    Remove or exclude legacy files that will always cause build failures
    in an SDK-style .NET 8 project. Returns list of fixes applied.
    """
    out = Path(output_dir)
    fixes = []

    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    # 1. Remove files that should not exist in .NET 8 SDK projects
    files_to_remove = [
        ("packages.config",  "Removed packages.config — not needed in SDK-style projects"),
        ("Global.asax",      "Removed Global.asax — startup hooks moved to Program.cs"),
        ("Global.asax.cs",   "Removed Global.asax.cs — startup logic merged into Program.cs"),
        ("Web.config",       "Removed Web.config — not needed in ASP.NET Core"),
        ("AssemblyInfo.cs",  "Removed AssemblyInfo.cs — auto-generated in SDK-style projects"),
    ]
    for filename, message in files_to_remove:
        for match in out.rglob(filename):
            if any(p.lower() in SKIP_FOLDERS for p in match.parts):
                continue
            # Keep Views/Web.config removal separate — it's inside Views folder
            try:
                match.unlink()
                fixes.append(message)
                progress(f"Build Validator: {message}")
            except Exception:
                pass

    # 2. Remove App_Start folder — not needed in ASP.NET Core
    for app_start in out.rglob("App_Start"):
        if app_start.is_dir() and not any(p.lower() in SKIP_FOLDERS for p in app_start.parts):
            try:
                shutil.rmtree(app_start)
                fixes.append("Removed App_Start folder — BundleConfig/RouteConfig/FilterConfig not needed in ASP.NET Core")
                progress("Build Validator: Removed App_Start folder")
            except Exception:
                pass

    # 3. Remove Properties folder if it only had AssemblyInfo.cs
    for props in out.rglob("Properties"):
        if props.is_dir() and not any(p.lower() in SKIP_FOLDERS for p in props.parts):
            remaining = [f for f in props.rglob("*") if f.is_file()]
            if not remaining:
                try:
                    props.rmdir()
                    fixes.append("Removed empty Properties folder")
                except Exception:
                    pass

    # 4. Fix .csproj — remove <Compile Update> for deleted files,
    #    remove <Content Update> for Web.config/Global.asax
    for csproj in out.rglob("*.csproj"):
        if any(p.lower() in SKIP_FOLDERS for p in csproj.parts):
            continue
        try:
            content = csproj.read_text(encoding="utf-8", errors="ignore")
            original = content

            # Remove <Compile Update="App_Start\..."> entries
            content = re.sub(
                r'\s*<Compile Update="App_Start\\[^"]*"\s*/>\s*\n?', '', content
            )
            # Remove <Compile Update="Global.asax.cs"> entries
            content = re.sub(
                r'\s*<Compile Update="Global\.asax\.cs"\s*/>\s*\n?', '', content
            )
            # Remove <Compile Update="Properties\AssemblyInfo.cs"> entries
            content = re.sub(
                r'\s*<Compile Update="Properties\\AssemblyInfo\.cs"\s*/>\s*\n?', '', content
            )
            # Remove <Content Update="Web.config"> entries
            content = re.sub(
                r'\s*<Content Update="Web\.config"\s*/>\s*\n?', '', content
            )
            # Remove <Content Update="Views\Web.config"> entries
            content = re.sub(
                r'\s*<Content Update="Views\\Web\.config"\s*/>\s*\n?', '', content
            )
            # Remove <Content Update="Global.asax"> entries
            content = re.sub(
                r'\s*<Content Update="Global\.asax"\s*/>\s*\n?', '', content
            )
            # Remove <Content Update="Scripts\..."> entries
            content = re.sub(
                r'\s*<Content Update="Scripts\\[^"]*"\s*/>\s*\n?', '', content
            )
            # Remove <Content Update="Content\..."> entries
            content = re.sub(
                r'\s*<Content Update="Content\\[^"]*"\s*/>\s*\n?', '', content
            )
            # Remove empty ItemGroup blocks
            content = re.sub(r'<ItemGroup>\s*</ItemGroup>\s*\n?', '', content)

            if content != original:
                csproj.write_text(content, encoding="utf-8")
                fixes.append(f"Cleaned {csproj.name} — removed legacy file references")
                progress(f"Build Validator: Cleaned {csproj.name}")
        except Exception:
            pass

    # 5. Fix Views/Web.config — remove it
    for views_webconfig in out.rglob("Views/Web.config"):
        if any(p.lower() in SKIP_FOLDERS for p in views_webconfig.parts):
            continue
        try:
            views_webconfig.unlink()
            fixes.append("Removed Views/Web.config")
        except Exception:
            pass

    return fixes


# ── Layer 2: Build loop with auto-fix ─────────────────────────────────────

def _run(cmd: list, cwd: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd
    )


def _find_csproj(output_dir: Path) -> Path | None:
    projects = [
        f for f in output_dir.rglob("*.csproj")
        if not any(p.lower() in SKIP_FOLDERS for p in f.parts)
    ]
    if not projects:
        return None
    web = [p for p in projects if "Microsoft.NET.Sdk.Web" in
           p.read_text(encoding="utf-8", errors="ignore")]
    return web[0] if web else projects[0]


def _parse_errors(output: str) -> list:
    """Extract structured error info from dotnet build output."""
    errors = []
    for match in re.finditer(
        r"([^:]+\.cs)\((\d+),\d+\):\s+error\s+(\w+):\s+(.+)", output
    ):
        errors.append({
            "file":    match.group(1).strip(),
            "line":    match.group(2),
            "code":    match.group(3),
            "message": match.group(4).strip(),
        })
    # Also catch general errors without file reference
    for match in re.finditer(r"error\s+([\w]+):\s+(.+)", output):
        code = match.group(1)
        if not any(e["code"] == code for e in errors):
            errors.append({"file": "", "line": "", "code": code, "message": match.group(2).strip()})
    return errors[:20]


def _auto_fix(csproj: Path, combined_output: str, progress_callback=None) -> list:
    """Apply known deterministic fixes based on build error codes."""
    fixes = []

    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    # NU1605 — package version downgrade conflict
    if "NU1605" in combined_output:
        matches = re.findall(
            r"NU1605.*?([\w\.]+)\s+from\s+([\d\.]+)\s+to\s+([\d\.]+)",
            combined_output
        )
        if matches:
            content = csproj.read_text(encoding="utf-8", errors="ignore")
            changed = False
            for _, _, required in matches:
                for pkg_match in re.finditer(
                    r'<PackageReference Include="([\w\.]+)"[^>]*Version="([\d\.]+)"',
                    content
                ):
                    pkg, ver = pkg_match.group(1), pkg_match.group(2)
                    if _version_less_than(ver, required):
                        content = re.sub(
                            rf'(<PackageReference Include="{re.escape(pkg)}"[^>]*Version=")[^"]*(")',
                            rf'\g<1>{required}\2', content
                        )
                        changed = True
            if changed:
                csproj.write_text(content, encoding="utf-8")
                fixes.append("Auto-fixed: Package version conflict (NU1605)")
                progress("Build Validator: Fixed NU1605 package version conflict")

    # CS0234 / CS0246 — invalid namespace or type not found
    if "CS0234" in combined_output or "CS0246" in combined_output:
        project_dir = csproj.parent
        bad_usings = set()
        for ns in re.findall(r"CS0234.*?namespace '([\w\.]+)'", combined_output):
            bad_usings.add(f"using {ns};")
        for t in re.findall(r"CS0246.*?type or namespace name '(\w+)'", combined_output):
            if t in ("HttpContext", "HttpRequest", "HttpResponse", "HttpServerUtility"):
                bad_usings.add("using System.Web;")
            if t in ("Controller", "ActionResult", "JsonResult", "ViewResult"):
                bad_usings.add("using System.Web.Mvc;")
            if t in ("ApiController", "IHttpActionResult"):
                bad_usings.add("using System.Web.Http;")
            if t in ("RoutePrefix",):
                bad_usings.add("using System.Web.Http;")
        if bad_usings:
            for cs_file in project_dir.rglob("*.cs"):
                try:
                    content = cs_file.read_text(encoding="utf-8", errors="ignore")
                    new_content = "\n".join(
                        line for line in content.splitlines()
                        if line.strip() not in bad_usings
                    )
                    if new_content != content:
                        cs_file.write_text(new_content, encoding="utf-8")
                except Exception:
                    pass
            fixes.append(f"Auto-fixed: Removed invalid using statements ({', '.join(bad_usings)})")
            progress(f"Build Validator: Removed invalid usings: {', '.join(bad_usings)}")

    # MSB3644 — reference assemblies not found (old framework references)
    if "MSB3644" in combined_output or "MSB3243" in combined_output:
        try:
            content = csproj.read_text(encoding="utf-8", errors="ignore")
            # Remove old Reference items pointing to System.Web etc
            cleaned = re.sub(
                r'\s*<Reference Include="System\.Web[^"]*"[^/]*/>\s*\n?', '', content
            )
            cleaned = re.sub(
                r'\s*<Reference Include="System\.Web[^"]*">.*?</Reference>\s*\n?',
                '', cleaned, flags=re.DOTALL
            )
            if cleaned != content:
                csproj.write_text(cleaned, encoding="utf-8")
                fixes.append("Auto-fixed: Removed old System.Web framework references (MSB3644)")
                progress("Build Validator: Removed old framework references")
        except Exception:
            pass

    return fixes


def _version_less_than(v1: str, v2: str) -> bool:
    try:
        return [int(x) for x in v1.split(".")] < [int(x) for x in v2.split(".")]
    except Exception:
        return False


def build_loop(output_dir: str, progress_callback=None) -> dict:
    """
    Run dotnet restore + build with auto-fix retry loop.
    Returns structured result.
    """
    out = Path(output_dir)

    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    if not shutil.which("dotnet"):
        return {
            "success": False,
            "skipped": True,
            "reason": "dotnet CLI not found — download the zip and run 'dotnet build' locally.",
            "errors": [],
            "auto_fixes": [],
            "output": "",
        }

    csproj = _find_csproj(out)
    if not csproj:
        return {
            "success": False,
            "skipped": True,
            "reason": "No .csproj found in migrated output.",
            "errors": [],
            "auto_fixes": [],
            "output": "",
        }

    project_dir = str(csproj.parent)
    all_fixes = []

    # Clean stale obj/bin before starting
    for folder in ["obj", "bin"]:
        stale = csproj.parent / folder
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)

    # Restore loop — up to 3 attempts
    for attempt in range(1, 4):
        progress(f"Build Validator: dotnet restore (attempt {attempt}/3)...")
        restore = _run(["dotnet", "restore", str(csproj), "--nologo"], project_dir)
        if restore.returncode == 0:
            progress("Build Validator: dotnet restore succeeded")
            break
        combined = restore.stdout + restore.stderr
        fixes = _auto_fix(csproj, combined, progress_callback)
        if fixes:
            all_fixes.extend(fixes)
            for folder in ["obj", "bin"]:
                stale = csproj.parent / folder
                if stale.exists():
                    shutil.rmtree(stale, ignore_errors=True)
            continue
        progress("Build Validator: Could not auto-fix restore errors")
        return {
            "success": False,
            "skipped": False,
            "reason": "dotnet restore failed",
            "errors": _parse_errors(combined),
            "auto_fixes": all_fixes,
            "output": combined,
        }

    # Build loop — up to 3 attempts
    for attempt in range(1, 4):
        progress(f"Build Validator: dotnet build (attempt {attempt}/3)...")
        build = _run(
            ["dotnet", "build", str(csproj), "--nologo", "-v", "m", "--no-restore"],
            project_dir
        )
        combined = build.stdout + build.stderr
        if build.returncode == 0:
            progress("Build Validator: dotnet build succeeded")
            return {
                "success": True,
                "skipped": False,
                "reason": "",
                "errors": [],
                "auto_fixes": all_fixes,
                "output": build.stdout,
            }
        fixes = _auto_fix(csproj, combined, progress_callback)
        if fixes:
            all_fixes.extend(fixes)
            continue
        # No more fixes available
        break

    progress("Build Validator: dotnet build failed — check Build Error AI Fixer")
    return {
        "success": False,
        "skipped": False,
        "reason": "dotnet build failed after auto-fix attempts",
        "errors": _parse_errors(combined),
        "auto_fixes": all_fixes,
        "output": combined,
    }


# ── Main entry point ──────────────────────────────────────────────────────

def run_build_validator(output_dir: str, progress_callback=None) -> dict:
    """
    Main entry point called from migration pipeline.
    Runs Layer 1 (pre-clean) then Layer 2 (build loop).
    Returns full result for reporter.
    """
    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    progress("Build Validator: Starting pre-build cleanup...")
    pre_fixes = pre_build_clean(output_dir, progress_callback)

    progress("Build Validator: Running build validation...")
    build_result = build_loop(output_dir, progress_callback)

    build_result["pre_clean_fixes"] = pre_fixes

    if build_result.get("skipped"):
        progress(f"Build Validator: Skipped — {build_result['reason']}")
    elif build_result["success"]:
        progress(f"Build Validator: Build passed after {len(pre_fixes)} pre-clean + {len(build_result['auto_fixes'])} auto-fixes")
    else:
        error_count = len(build_result.get("errors", []))
        progress(f"Build Validator: Build failed — {error_count} error(s) remaining")

    return build_result

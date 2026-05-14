from agents.llm import ask_with_system
from pathlib import Path
import re

SYSTEM_PROMPT = """You are a .NET migration expert. 
Analyze C# code and identify:
1. Current .NET version and framework
2. Dependencies and NuGet packages
3. Patterns that need updating
4. Breaking changes required
5. Migration complexity (Low/Medium/High)

Be concise and structured in your response."""

SKIP_FOLDERS = {"obj", "bin", ".vs", ".git", "node_modules", "packages"}

def read_files(upload_dir: str) -> dict:
    files = {}
    upload_path = Path(upload_dir)
    for ext in ["*.cs", "*.csproj", "*.sln", "*.config"]:
        for file in upload_path.rglob(ext):
            if any(part.lower() in SKIP_FOLDERS for part in file.parts):
                continue
            try:
                files[str(file.relative_to(upload_path))] = file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
    return files


def _detect_patterns(upload_path: Path) -> list:
    checks = [
        ("Startup.cs",      "Startup class",          "Convert to minimal hosting or modern Program.cs",          "Medium"),
        ("packages.config", "packages.config",         "Migrate packages.config to PackageReference",              "High"),
        ("web.config",      "web.config",              "Review IIS/system.web settings for ASP.NET Core hosting",  "Medium"),
        ("Global.asax",     "Global.asax",             "Move application startup hooks to ASP.NET Core pipeline",  "High"),
    ]
    found = []
    all_files = [f for f in upload_path.rglob("*") if f.is_file()
                 and not any(p.lower() in SKIP_FOLDERS for p in f.parts)]
    names = {f.name.lower(): f for f in all_files}
    for filename, title, action, severity in checks:
        if filename.lower() in names:
            found.append({"title": title, "path": filename, "action": action, "severity": severity})
    for f in all_files:
        if f.suffix.lower() != ".cs":
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")[:12000]
        except Exception:
            continue
        if "System.Web" in text:
            found.append({"title": "System.Web usage", "path": f.name,
                          "action": "Replace with ASP.NET Core abstractions", "severity": "High"})
        if "ConfigurationManager" in text:
            found.append({"title": "ConfigurationManager usage", "path": f.name,
                          "action": "Move settings to IConfiguration/options pattern", "severity": "Medium"})
    return found[:30]


def _detect_ui_profile(upload_path: Path) -> dict:
    all_files = [f for f in upload_path.rglob("*") if f.is_file()
                 and not any(p.lower() in SKIP_FOLDERS for p in f.parts)]

    cshtml_files  = [f for f in all_files if f.suffix.lower() == ".cshtml"]
    aspx_files    = [f for f in all_files if f.suffix.lower() in {".aspx", ".ascx", ".master"}]
    razor_files   = [f for f in all_files if f.suffix.lower() == ".razor"]
    has_angular   = any(f.name == "angular.json" for f in all_files)
    has_react     = any(f.name == "package.json" for f in all_files)
    has_bundling  = any(f.name.lower() == "bundleconfig.cs" for f in all_files)

    # Count HTML helpers in cshtml files
    html_helper_count = 0
    for f in cshtml_files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            html_helper_count += len(re.findall(r'@Html\.', text))
            if any(s in text for s in ["@Scripts.Render", "@Styles.Render"]):
                has_bundling = True
        except Exception:
            pass

    # Determine primary UI type
    if aspx_files:
        ui_type = "webforms"
        warning = (f"Web Forms UI detected ({len(aspx_files)} page(s)) — "
                   f"will be automatically converted to .NET 8 Razor Pages.")
        support = "not_supported"
    elif cshtml_files:
        ui_type = "razor_mvc"
        warning = (f"Razor MVC UI detected ({len(cshtml_files)} view(s)) — "
                   f"will be automatically migrated to .NET 8.")
        support = "partial"
    elif razor_files:
        ui_type = "blazor"
        warning = (f"Blazor UI detected ({len(razor_files)} component(s)) — "
                   f"will be automatically migrated to .NET 8.")
        support = "partial"
    elif has_angular:
        ui_type = "angular"
        warning = "Angular frontend detected — backend will be migrated to .NET 8, frontend stays as-is."
        support = "backend_only"
    elif has_react:
        ui_type = "react"
        warning = "React frontend detected — backend will be migrated to .NET 8, frontend stays as-is."
        support = "backend_only"
    else:
        ui_type = "none"
        warning = ""
        support = "full"

    return {
        "ui_type":           ui_type,
        "support":           support,
        "warning":           warning,
        "cshtml_count":      len(cshtml_files),
        "aspx_count":        len(aspx_files),
        "razor_count":       len(razor_files),
        "html_helper_count": html_helper_count,
        "has_bundling":      has_bundling,
        "has_angular":       has_angular,
        "has_react":         has_react,
    }


def _inspect_csproj(path: Path, root: Path) -> dict:
    content = path.read_text(encoding="utf-8", errors="ignore")
    packages, target_framework = [], ""
    tf_match = re.search(r"<TargetFrameworks?>(.*?)</TargetFrameworks?>", content, re.I | re.S)
    if tf_match:
        target_framework = tf_match.group(1).strip()
    for m in re.finditer(r'<PackageReference\s+Include="([^"]+)"(?:[^>]+Version="([^"]+)")?', content, re.I):
        packages.append({"name": m.group(1), "version": m.group(2) or ""})
    return {
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "target_framework": target_framework,
        "packages": packages,
        "sdk_style": "<Project Sdk=" in content,
        "is_web": "Microsoft.NET.Sdk.Web" in content or any(
            p["name"].startswith("Microsoft.AspNetCore") for p in packages),
    }


def analyze(upload_dir: str, from_version: str, to_version: str) -> dict:
    upload_path = Path(upload_dir)
    files = read_files(upload_dir)

    if not files:
        return {
            "success": False,
            "error": "No C# files found in upload directory",
            "project_count": 0, "source_file_count": 0, "total_file_count": 0,
            "projects": [], "packages": [], "patterns": [], "frameworks": [],
            "complexity": {"score": 0, "level": "Low"},
            "recommended_path": "Upload a project zip or source files to begin analysis.",
            "from_version": from_version, "to_version": to_version,
            "ui_profile": {"ui_type": "none", "support": "full", "warning": ""},
        }

    csproj_files = [upload_path / p for p in files if p.endswith(".csproj")]
    cs_files     = [upload_path / p for p in files if p.endswith(".cs")]
    sln_files    = [upload_path / p for p in files if p.endswith(".sln")]

    projects  = [_inspect_csproj(f, upload_path) for f in csproj_files if f.exists()]
    packages  = sorted({pkg["name"] for proj in projects for pkg in proj["packages"]})
    frameworks = sorted({proj["target_framework"] for proj in projects if proj["target_framework"] and proj["is_web"]})
    if not frameworks:
        frameworks = sorted({proj["target_framework"] for proj in projects if proj["target_framework"]})
    patterns  = _detect_patterns(upload_path)

    # Complexity score
    points = len(projects) * 8 + len(cs_files) // 4 + len(patterns) * 10
    if "Framework" in from_version:
        points += 25
    if "10" in to_version or "9" in to_version:
        points += 5
    points = min(points, 100)
    level = "High" if points >= 70 else "Medium" if points >= 35 else "Low"

    if "Framework" in from_version and level == "High":
        recommended = (f"Use staged migration: compile on .NET Framework first, port projects to "
                       f"SDK style, then move to {to_version} with build-fix iterations.")
    else:
        recommended = (f"Direct migration to {to_version} is reasonable with "
                       f"restore/build/test validation gates.")

    # LLM analysis summary (best-effort — does not block if LLM fails)
    analysis_text = ""
    try:
        code_summary = ""
        for name, content in list(files.items())[:8]:
            code_summary += f"\n--- {name} ---\n{content[:800]}\n"
        prompt = (f"Analyze this .NET code for migration from {from_version} to {to_version}:\n"
                  f"{code_summary}\nProvide top 3 breaking changes needed in 2 sentences each.")
        analysis_text = ask_with_system(SYSTEM_PROMPT, prompt)
    except Exception:
        pass

    return {
        "success": True,
        "from_version": from_version,
        "to_version": to_version,
        "project_count": len(projects),
        "source_file_count": len(cs_files),
        "total_file_count": len(files),
        "solution_files": [str(f.relative_to(upload_path)).replace("\\", "/") for f in sln_files if f.exists()],
        "projects": projects,
        "packages": packages,
        "frameworks": frameworks,
        "patterns": patterns,
        "complexity": {"score": points, "level": level},
        "recommended_path": recommended,
        "analysis": analysis_text,
        "ui_profile": _detect_ui_profile(upload_path),
    }

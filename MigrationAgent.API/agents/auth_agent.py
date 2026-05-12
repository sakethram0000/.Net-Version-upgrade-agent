"""
Auth Agent — runs after LLM migration, before Fix Agent.
Detects auth patterns in the original upload, injects correct .NET 8
auth templates into the migrated output, then verifies the result.
No LLM involved — fully deterministic.
"""
from pathlib import Path
import re

SKIP_FOLDERS = {"obj", "bin", ".vs", ".git", "node_modules", "packages"}

# ── Auth type constants ───────────────────────────────────────────────────
AUTH_NONE       = "none"
AUTH_JWT        = "jwt"
AUTH_IDENTITY   = "identity"
AUTH_COOKIE     = "cookie"
AUTH_WINDOWS    = "windows"
AUTH_FORMS      = "forms"
AUTH_CUSTOM     = "custom"
AUTH_AUTHORIZE  = "authorize_only"   # [Authorize] present but no setup detected


# ── .NET 8 auth templates ─────────────────────────────────────────────────

JWT_SERVICES_TEMPLATE = """
// Auth Agent: JWT Bearer Authentication
builder.Services.AddAuthentication(options =>
{{
    options.DefaultAuthenticateScheme = JwtBearerDefaults.AuthenticationScheme;
    options.DefaultChallengeScheme    = JwtBearerDefaults.AuthenticationScheme;
}})
.AddJwtBearer(options =>
{{
    options.TokenValidationParameters = new TokenValidationParameters
    {{
        ValidateIssuer           = true,
        ValidateAudience         = true,
        ValidateLifetime         = true,
        ValidateIssuerSigningKey = true,
        ValidIssuer              = builder.Configuration["JwtSettings:Issuer"],
        ValidAudience            = builder.Configuration["JwtSettings:Audience"],
        IssuerSigningKey         = new SymmetricSecurityKey(
            System.Text.Encoding.UTF8.GetBytes(
                builder.Configuration["JwtSettings:Secret"] ?? string.Empty)),
    }};
}});
builder.Services.AddAuthorization();
"""

JWT_MIDDLEWARE_TEMPLATE = """
// Auth Agent: JWT middleware — order matters
app.UseAuthentication();
app.UseAuthorization();
"""

JWT_USING_TEMPLATE = """using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.IdentityModel.Tokens;
"""

IDENTITY_SERVICES_TEMPLATE = """
// Auth Agent: ASP.NET Core Identity
builder.Services.AddIdentity<IdentityUser, IdentityRole>(options =>
{{
    options.Password.RequireDigit           = true;
    options.Password.RequiredLength         = 8;
    options.Password.RequireNonAlphanumeric = false;
}})
.AddEntityFrameworkStores<{ctx_name}>()
.AddDefaultTokenProviders();
builder.Services.AddAuthorization();
"""

IDENTITY_MIDDLEWARE_TEMPLATE = """
// Auth Agent: Identity middleware — order matters
app.UseAuthentication();
app.UseAuthorization();
"""

IDENTITY_USING_TEMPLATE = """using Microsoft.AspNetCore.Identity;
"""

COOKIE_SERVICES_TEMPLATE = """
// Auth Agent: Cookie Authentication (migrated from Forms Authentication)
builder.Services.AddAuthentication(CookieAuthenticationDefaults.AuthenticationScheme)
    .AddCookie(options =>
    {{
        options.LoginPath  = "/Account/Login";
        options.LogoutPath = "/Account/Logout";
        options.ExpireTimeSpan = TimeSpan.FromMinutes(60);
    }});
builder.Services.AddAuthorization();
"""

COOKIE_MIDDLEWARE_TEMPLATE = """
// Auth Agent: Cookie middleware — order matters
app.UseAuthentication();
app.UseAuthorization();
"""

COOKIE_USING_TEMPLATE = """using Microsoft.AspNetCore.Authentication.Cookies;
"""

AUTHORIZE_MIDDLEWARE_TEMPLATE = """
// Auth Agent: Authorization middleware
app.UseAuthentication();
app.UseAuthorization();
"""


# ── Detection ─────────────────────────────────────────────────────────────

def detect_auth(upload_dir: str) -> dict:
    """
    Scan the original uploaded project and build an auth profile.
    Returns a dict describing what auth patterns were found.
    """
    upload_path = Path(upload_dir)
    profile = {
        "type":               AUTH_NONE,
        "has_authorize":      False,
        "roles":              [],
        "policies":           [],
        "protected_files":    [],
        "jwt_config_keys":    [],
        "identity_context":   None,
        "custom_middleware":  [],
        "notes":              [],
    }

    all_cs   = _collect_cs(upload_path)
    all_conf = _collect_config(upload_path)

    # Check for [Authorize] usage
    for path, content in all_cs.items():
        if "[Authorize" in content:
            profile["has_authorize"] = True
            profile["protected_files"].append(path)
            for m in re.findall(r'\[Authorize\(Roles\s*=\s*"([^"]+)"\)', content):
                for role in m.split(","):
                    r = role.strip()
                    if r and r not in profile["roles"]:
                        profile["roles"].append(r)
            for m in re.findall(r'\[Authorize\(Policy\s*=\s*"([^"]+)"\)', content):
                if m not in profile["policies"]:
                    profile["policies"].append(m)

    # Detect JWT
    jwt_signals = [
        "AddJwtBearer", "JwtBearerDefaults", "JwtSecurityToken",
        "TokenValidationParameters", "IssuerSigningKey",
        "System.IdentityModel.Tokens.Jwt",
    ]
    for path, content in all_cs.items():
        if any(s in content for s in jwt_signals):
            profile["type"] = AUTH_JWT
            # Try to find config keys used
            for m in re.findall(r'configuration\["([^"]+)"\]', content, re.I):
                if "jwt" in m.lower() or "secret" in m.lower() or "token" in m.lower():
                    if m not in profile["jwt_config_keys"]:
                        profile["jwt_config_keys"].append(m)
            break

    # Detect ASP.NET Identity
    identity_signals = [
        "AddIdentity", "UserManager", "RoleManager",
        "IdentityUser", "IdentityRole", "SignInManager",
    ]
    for path, content in all_cs.items():
        if any(s in content for s in identity_signals):
            if profile["type"] == AUTH_NONE:
                profile["type"] = AUTH_IDENTITY
            # Find DbContext name
            m = re.search(r'AddEntityFrameworkStores<(\w+)>', content)
            if m:
                profile["identity_context"] = m.group(1)
            break

    # Detect Cookie / Forms auth
    cookie_signals = ["AddCookie", "CookieAuthenticationDefaults", "FormsAuthentication"]
    forms_signals  = ["FormsAuthentication", "<authentication mode=\"Forms\""]
    for path, content in {**all_cs, **all_conf}.items():
        if any(s in content for s in forms_signals):
            if profile["type"] == AUTH_NONE:
                profile["type"] = AUTH_FORMS
            break
        if any(s in content for s in cookie_signals):
            if profile["type"] == AUTH_NONE:
                profile["type"] = AUTH_COOKIE
            break

    # Detect Windows auth
    windows_signals = ["WindowsAuthentication", "UseWindowsAuthentication", "Negotiate", "Kerberos"]
    for path, content in {**all_cs, **all_conf}.items():
        if any(s in content for s in windows_signals):
            if profile["type"] == AUTH_NONE:
                profile["type"] = AUTH_WINDOWS
            break

    # Detect custom auth middleware
    custom_signals = ["IMiddleware", "InvokeAsync", "HttpContext.User", "AuthenticationMiddleware"]
    for path, content in all_cs.items():
        if "Middleware" in path and any(s in content for s in custom_signals):
            if "Auth" in path or "auth" in path.lower():
                profile["custom_middleware"].append(path)

    if profile["custom_middleware"] and profile["type"] == AUTH_NONE:
        profile["type"] = AUTH_CUSTOM

    # If [Authorize] found but no setup detected
    if profile["has_authorize"] and profile["type"] == AUTH_NONE:
        profile["type"] = AUTH_AUTHORIZE

    # Build notes
    if profile["type"] == AUTH_NONE:
        profile["notes"].append("No authentication detected in original project.")
    if profile["type"] == AUTH_WINDOWS:
        profile["notes"].append("Windows Authentication is environment-specific — manual configuration required.")
    if profile["type"] == AUTH_CUSTOM:
        profile["notes"].append("Custom authentication middleware detected — manual migration required.")
    if profile["roles"]:
        profile["notes"].append(f"Roles detected: {', '.join(profile['roles'])} — ensure roles are seeded in your database.")
    if profile["policies"]:
        profile["notes"].append(f"Policies detected: {', '.join(profile['policies'])} — re-register policies in Program.cs.")

    return profile


# ── Template injection ────────────────────────────────────────────────────

def inject_auth(output_dir: str, profile: dict, progress_callback=None) -> dict:
    """
    Inject the correct .NET 8 auth template into the migrated Program.cs
    based on the detected auth profile.
    Returns a result dict with what was done.
    """
    out = Path(output_dir)
    result = {
        "auth_type":    profile["type"],
        "injected":     False,
        "changes":      [],
        "warnings":     list(profile["notes"]),
        "skipped":      False,
        "skip_reason":  "",
    }

    # Nothing to inject for these types
    if profile["type"] in (AUTH_NONE, AUTH_WINDOWS, AUTH_CUSTOM):
        result["skipped"]     = True
        result["skip_reason"] = profile["notes"][0] if profile["notes"] else f"Auth type '{profile['type']}' requires manual handling."
        return result

    # Find Program.cs in output
    program_cs = _find_program_cs(out)
    if not program_cs:
        result["skipped"]     = True
        result["skip_reason"] = "Program.cs not found in migrated output."
        return result

    if progress_callback:
        progress_callback(f"Auth Agent: Injecting {profile['type']} auth into {program_cs.name}...")

    content = program_cs.read_text(encoding="utf-8", errors="ignore")
    original = content

    if profile["type"] == AUTH_JWT:
        content, changes = _inject_jwt(content, profile)
        result["changes"].extend(changes)
        # Add JWT package to csproj if missing
        _ensure_package(out, "Microsoft.AspNetCore.Authentication.JwtBearer", "8.0.4")
        _ensure_package(out, "Microsoft.IdentityModel.Tokens", "7.5.1")
        result["changes"].append("Ensured Microsoft.AspNetCore.Authentication.JwtBearer 8.0.4 in .csproj")
        # Warn about secret key
        result["warnings"].append("Set JwtSettings:Secret, JwtSettings:Issuer and JwtSettings:Audience in appsettings.json before deploying.")

    elif profile["type"] == AUTH_IDENTITY:
        ctx = profile.get("identity_context") or "ApplicationDbContext"
        content, changes = _inject_identity(content, ctx)
        result["changes"].extend(changes)
        _ensure_package(out, "Microsoft.AspNetCore.Identity.EntityFrameworkCore", "8.0.4")
        result["changes"].append("Ensured Microsoft.AspNetCore.Identity.EntityFrameworkCore 8.0.4 in .csproj")

    elif profile["type"] in (AUTH_COOKIE, AUTH_FORMS):
        content, changes = _inject_cookie(content)
        result["changes"].extend(changes)
        result["warnings"].append("Cookie auth migrated from Forms Authentication — verify login/logout paths match your controllers.")

    elif profile["type"] == AUTH_AUTHORIZE:
        content, changes = _inject_authorize_only(content)
        result["changes"].extend(changes)

    if content != original:
        program_cs.write_text(content, encoding="utf-8")
        result["injected"] = True

    return result


# ── Verification ──────────────────────────────────────────────────────────

def verify_auth(output_dir: str, profile: dict, inject_result: dict) -> dict:
    """
    Verify the auth setup in the migrated output is correct.
    Returns a list of checks with pass/fail.
    """
    out = Path(output_dir)
    checks = []

    if profile["type"] in (AUTH_NONE,):
        checks.append(_check("No auth required", True, "No authentication in original project — nothing to verify."))
        return {"checks": checks, "passed": 1, "total": 1}

    if profile["type"] in (AUTH_WINDOWS, AUTH_CUSTOM):
        checks.append(_check("Manual review required", False,
            inject_result.get("skip_reason", "This auth type requires manual migration.")))
        return {"checks": checks, "passed": 0, "total": 1}

    program_cs = _find_program_cs(out)
    if not program_cs:
        checks.append(_check("Program.cs found", False, "Program.cs missing from migrated output."))
        return {"checks": checks, "passed": 0, "total": 1}

    content = program_cs.read_text(encoding="utf-8", errors="ignore")

    # Check UseAuthentication exists
    has_use_auth = "UseAuthentication()" in content
    checks.append(_check("UseAuthentication() present", has_use_auth,
        "app.UseAuthentication() must be called in Program.cs"))

    # Check UseAuthorization exists
    has_use_authz = "UseAuthorization()" in content
    checks.append(_check("UseAuthorization() present", has_use_authz,
        "app.UseAuthorization() must be called in Program.cs"))

    # Check middleware ORDER — UseAuthentication must come before UseAuthorization
    if has_use_auth and has_use_authz:
        auth_pos  = content.index("UseAuthentication()")
        authz_pos = content.index("UseAuthorization()")
        order_ok  = auth_pos < authz_pos
        checks.append(_check("Middleware order correct", order_ok,
            "UseAuthentication() must come BEFORE UseAuthorization()"))

    # Check AddAuthentication registered
    has_add_auth = "AddAuthentication" in content
    checks.append(_check("AddAuthentication() registered", has_add_auth,
        "builder.Services.AddAuthentication() must be registered"))

    # Check AddAuthorization registered
    has_add_authz = "AddAuthorization" in content
    checks.append(_check("AddAuthorization() registered", has_add_authz,
        "builder.Services.AddAuthorization() must be registered"))

    # JWT-specific checks
    if profile["type"] == AUTH_JWT:
        has_jwt_pkg = _has_package(out, "Microsoft.AspNetCore.Authentication.JwtBearer")
        checks.append(_check("JwtBearer package in .csproj", has_jwt_pkg,
            "Add Microsoft.AspNetCore.Authentication.JwtBearer to .csproj"))
        has_jwt_bearer = "AddJwtBearer" in content
        checks.append(_check("AddJwtBearer() configured", has_jwt_bearer,
            "JWT bearer options must be configured in AddAuthentication()"))

    # Identity-specific checks
    if profile["type"] == AUTH_IDENTITY:
        has_identity = "AddIdentity" in content
        checks.append(_check("AddIdentity() configured", has_identity,
            "builder.Services.AddIdentity() must be configured"))
        has_identity_pkg = _has_package(out, "Microsoft.AspNetCore.Identity.EntityFrameworkCore")
        checks.append(_check("Identity EF package in .csproj", has_identity_pkg,
            "Add Microsoft.AspNetCore.Identity.EntityFrameworkCore to .csproj"))

    # Check [Authorize] attributes preserved
    if profile["has_authorize"] and profile["protected_files"]:
        preserved = 0
        for rel_path in profile["protected_files"]:
            out_file = out / rel_path
            if out_file.exists():
                if "[Authorize" in out_file.read_text(encoding="utf-8", errors="ignore"):
                    preserved += 1
        total_protected = len(profile["protected_files"])
        checks.append(_check(
            f"[Authorize] preserved ({preserved}/{total_protected} files)",
            preserved == total_protected,
            f"[Authorize] attributes must be preserved in all {total_protected} protected controller(s)"
        ))

    passed = sum(1 for c in checks if c["passed"])
    return {"checks": checks, "passed": passed, "total": len(checks)}


# ── Main entry point ──────────────────────────────────────────────────────

def run_auth_agent(upload_dir: str, output_dir: str, progress_callback=None) -> dict:
    """
    Main entry point called from migration pipeline.
    Returns full auth migration result.
    """
    if progress_callback:
        progress_callback("Auth Agent: Detecting authentication patterns...")

    profile       = detect_auth(upload_dir)
    inject_result = inject_auth(output_dir, profile, progress_callback)

    if progress_callback:
        progress_callback("Auth Agent: Verifying authentication setup...")

    verify_result = verify_auth(output_dir, profile, inject_result)

    passed  = verify_result["passed"]
    total   = verify_result["total"]
    status  = "passed" if passed == total else "needs_review" if passed > 0 else "failed"

    if progress_callback:
        progress_callback(f"Auth Agent: {passed}/{total} auth checks passed.")

    return {
        "auth_type":    profile["type"],
        "has_authorize": profile["has_authorize"],
        "protected_files": profile["protected_files"],
        "roles":        profile["roles"],
        "policies":     profile["policies"],
        "injected":     inject_result["injected"],
        "changes":      inject_result["changes"],
        "warnings":     inject_result["warnings"],
        "checks":       verify_result["checks"],
        "passed":       passed,
        "total":        total,
        "status":       status,
        "summary":      f"{profile['type'].upper()} auth — {passed}/{total} checks passed.",
    }


# ── Injection helpers ─────────────────────────────────────────────────────

def _inject_jwt(content: str, profile: dict) -> tuple:
    changes = []

    # Add usings if missing
    if "JwtBearerDefaults" not in content:
        content = JWT_USING_TEMPLATE + content
        changes.append("Added JWT using statements to Program.cs")

    # Inject services before builder.Build()
    if "AddJwtBearer" not in content:
        content = _inject_before_build(content, JWT_SERVICES_TEMPLATE)
        changes.append("Injected JWT Bearer authentication services into Program.cs")

    # Inject middleware — ensure correct order
    content, mw_changes = _ensure_auth_middleware(content, JWT_MIDDLEWARE_TEMPLATE)
    changes.extend(mw_changes)

    return content, changes


def _inject_identity(content: str, ctx_name: str) -> tuple:
    changes = []

    if "AddIdentity" not in content:
        services = IDENTITY_SERVICES_TEMPLATE.format(ctx_name=ctx_name)
        content = IDENTITY_USING_TEMPLATE + content
        content = _inject_before_build(content, services)
        changes.append(f"Injected ASP.NET Core Identity services (DbContext: {ctx_name}) into Program.cs")

    content, mw_changes = _ensure_auth_middleware(content, IDENTITY_MIDDLEWARE_TEMPLATE)
    changes.extend(mw_changes)

    return content, changes


def _inject_cookie(content: str) -> tuple:
    changes = []

    if "AddCookie" not in content and "AddAuthentication" not in content:
        content = COOKIE_USING_TEMPLATE + content
        content = _inject_before_build(content, COOKIE_SERVICES_TEMPLATE)
        changes.append("Injected Cookie authentication services into Program.cs (migrated from Forms Auth)")

    content, mw_changes = _ensure_auth_middleware(content, COOKIE_MIDDLEWARE_TEMPLATE)
    changes.extend(mw_changes)

    return content, changes


def _inject_authorize_only(content: str) -> tuple:
    """Project uses [Authorize] but has no auth setup — add minimal authorization."""
    changes = []
    if "AddAuthorization" not in content:
        content = _inject_before_build(content, "\n// Auth Agent: Authorization services\nbuilder.Services.AddAuthorization();\n")
        changes.append("Added AddAuthorization() — project uses [Authorize] attributes")

    content, mw_changes = _ensure_auth_middleware(content, AUTHORIZE_MIDDLEWARE_TEMPLATE)
    changes.extend(mw_changes)

    return content, changes


def _ensure_auth_middleware(content: str, template: str) -> tuple:
    """
    Ensure UseAuthentication + UseAuthorization are present and in correct order.
    If already present, fix order if wrong. If missing, inject before app.Run().
    """
    changes = []
    has_use_auth  = "UseAuthentication()" in content
    has_use_authz = "UseAuthorization()" in content

    if has_use_auth and has_use_authz:
        # Check order — fix if wrong
        auth_pos  = content.index("UseAuthentication()")
        authz_pos = content.index("UseAuthorization()")
        if auth_pos > authz_pos:
            # Remove both and re-inject in correct order
            content = content.replace("app.UseAuthentication();", "")
            content = content.replace("app.UseAuthorization();", "")
            content = _inject_before_run(content, "\napp.UseAuthentication();\napp.UseAuthorization();\n")
            changes.append("Fixed middleware order: UseAuthentication() moved before UseAuthorization()")
        return content, changes

    if not has_use_auth or not has_use_authz:
        content = _inject_before_run(content, template)
        changes.append("Injected UseAuthentication() and UseAuthorization() middleware into Program.cs")

    return content, changes


def _inject_before_build(content: str, snippet: str) -> str:
    """Inject snippet just before var app = builder.Build()"""
    marker = "var app = builder.Build()"
    if marker in content:
        return content.replace(marker, snippet + "\n" + marker, 1)
    # Fallback — inject before app.Run()
    return _inject_before_run(content, snippet)


def _inject_before_run(content: str, snippet: str) -> str:
    """Inject snippet just before app.Run()"""
    marker = "app.Run()"
    if marker in content:
        return content.replace(marker, snippet + "\n" + marker, 1)
    # Last resort — append
    return content + "\n" + snippet


def _ensure_package(output_dir: Path, package_name: str, version: str):
    """Add a NuGet package to .csproj if not already present."""
    for csproj in output_dir.rglob("*.csproj"):
        if any(p.lower() in SKIP_FOLDERS for p in csproj.parts):
            continue
        try:
            content = csproj.read_text(encoding="utf-8", errors="ignore")
            if package_name not in content:
                ref = f'    <PackageReference Include="{package_name}" Version="{version}" />'
                if "</ItemGroup>" in content:
                    content = content.replace("</ItemGroup>", f"{ref}\n  </ItemGroup>", 1)
                else:
                    content += f'\n  <ItemGroup>\n{ref}\n  </ItemGroup>\n'
                csproj.write_text(content, encoding="utf-8")
        except Exception:
            pass


def _has_package(output_dir: Path, package_name: str) -> bool:
    for csproj in output_dir.rglob("*.csproj"):
        try:
            if package_name in csproj.read_text(encoding="utf-8", errors="ignore"):
                return True
        except Exception:
            pass
    return False


def _find_program_cs(output_dir: Path) -> Path | None:
    for f in output_dir.rglob("Program.cs"):
        if not any(p.lower() in SKIP_FOLDERS for p in f.parts):
            return f
    return None


def _collect_cs(root: Path) -> dict:
    files = {}
    for f in root.rglob("*.cs"):
        if any(p.lower() in SKIP_FOLDERS for p in f.parts):
            continue
        try:
            files[str(f.relative_to(root))] = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
    return files


def _collect_config(root: Path) -> dict:
    files = {}
    for pattern in ["*.config", "appsettings*.json", "web.config"]:
        for f in root.rglob(pattern):
            if any(p.lower() in SKIP_FOLDERS for p in f.parts):
                continue
            try:
                files[str(f.relative_to(root))] = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
    return files


def _check(name: str, passed: bool, description: str) -> dict:
    return {"name": name, "passed": passed, "description": description}

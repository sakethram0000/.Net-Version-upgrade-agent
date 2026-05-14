from agents.llm import ask_with_system
from pathlib import Path
import re
import time
from typing import Callable, Optional

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "outputs" / "migrated"

SYSTEM_CS = """You are a .NET 8 migration expert. Migrate C# code to .NET 8 / C# 12.
Rules:
- Use file-scoped namespaces (namespace Foo.Bar; not namespace Foo.Bar { })
- Replace obsolete APIs with .NET 8 equivalents
- Keep ALL business logic intact — do not remove any methods or properties
- Replace System.Web.Mvc with Microsoft.AspNetCore.Mvc
- Replace System.Web.Http with Microsoft.AspNetCore.Mvc
- Replace HttpContext.Current with IHttpContextAccessor injected via constructor
- Replace ConfigurationManager / WebConfigurationManager with IConfiguration injected via constructor
- Replace [System.Web.Http.Route] with [Microsoft.AspNetCore.Mvc.Route]
- Replace ActionResult from System.Web.Mvc with IActionResult from Microsoft.AspNetCore.Mvc
- Replace JsonResult(obj) with Ok(obj)
- Replace HttpNotFound() with NotFound()
- Replace new HttpStatusCodeResult(400) with BadRequest()
- Replace Request.QueryString["key"] with Request.Query["key"]
- Replace Request.Form["key"] with Request.Form["key"] (same)
- Replace Response.Redirect with return Redirect()
- Remove [ValidateAntiForgeryToken] if it causes issues in API controllers
- Keep all using statements that are valid in .NET 8
- Return ONLY the migrated C# code inside a ```csharp block. Nothing else."""

SYSTEM_PROGRAM = """You are a .NET 8 migration expert. Your job is to produce a single Program.cs using .NET 8 minimal hosting.
Rules:
- Use WebApplication.CreateBuilder(args)
- Move ALL services from Startup.ConfigureServices into builder.Services
- Move ALL middleware from Startup.Configure into app.Use...
- End with app.Run()
- NO Startup class, NO CreateHostBuilder, NO IHostBuilder
- Return ONLY the complete Program.cs code inside a ```csharp block. Nothing else."""

SYSTEM_CSPROJ = """You are a .NET 8 migration expert. Migrate .csproj to .NET 8 SDK style.
Rules:
- Set <TargetFramework>net8.0</TargetFramework>
- Add <Nullable>enable</Nullable> and <ImplicitUsings>enable</ImplicitUsings>
- Keep the Sdk attribute on the Project tag exactly as: <Project Sdk="Microsoft.NET.Sdk.Web">
- REMOVE these packages completely: Microsoft.AspNetCore.SpaServices.Extensions, Npgsql.EntityFrameworkCore.PostgreSQL.Design, Microsoft.AspNet.Mvc, Microsoft.AspNet.WebApi, Microsoft.AspNet.WebPages, Microsoft.Web.Infrastructure
- Set Microsoft.EntityFrameworkCore and all EF Core packages to Version 8.0.4
- Set Npgsql.EntityFrameworkCore.PostgreSQL to Version 8.0.4
- Set Microsoft.AspNetCore.Authentication.JwtBearer to Version 8.0.4
- Set Swashbuckle.AspNetCore to Version 6.5.0
- Remove any <Target> blocks related to SPA, webpack, or npm
- Remove any <Reference> items pointing to System.Web or old .NET Framework assemblies
- Keep SDK-style format, clean and minimal
- Return ONLY the migrated XML inside a ```xml block. Nothing else."""

SYSTEM_REVIEWER = """You are a .NET 8 code reviewer. Review migrated C# code and fix any remaining issues.
Rules:
- Fix any remaining System.Web references
- Fix any remaining old-style namespaces (convert block namespace to file-scoped)
- Fix any remaining UseEndpoints — replace with app.MapControllers()
- Fix any remaining AddSpaStaticFiles or UseSpa calls — remove them
- Fix any remaining HttpContext.Current — replace with IHttpContextAccessor
- Fix any remaining ConfigurationManager — replace with IConfiguration
- Ensure all using statements are valid for .NET 8
- Keep ALL business logic intact — do not remove any methods or properties
- If code is already correct, return it as-is
- Return ONLY the corrected code inside a ```csharp block. Nothing else."""

# Folders to always skip during file reading
SKIP_FOLDERS = {"obj", "bin", ".vs", ".git", "node_modules", ".idea", "packages"}

# Extensions that get LLM migration
CODE_EXTENSIONS = {".cs", ".csproj", ".sln", ".config"}

# Extensions to copy as-is (no LLM, no modification)
COPY_EXTENSIONS = {
    ".cshtml", ".razor", ".json", ".xml", ".yaml", ".yml",
    ".html", ".htm", ".css", ".js", ".ts", ".jsx", ".tsx",
    ".txt", ".md", ".ico", ".png", ".jpg", ".jpeg", ".gif",
    ".svg", ".woff", ".woff2", ".ttf", ".eot", ".map",
    ".aspx", ".ascx", ".master", ".resx", ".edmx",
}

# Folders to always skip
SKIP_COPY_FOLDERS = {"obj", "bin", ".vs", ".git", "node_modules", ".idea", "packages"}

def read_files_recursive(upload_dir: str) -> dict:
    """Read only code files that need LLM migration."""
    files = {}
    upload_path = Path(upload_dir)
    for file in upload_path.rglob("*"):
        if not file.is_file():
            continue
        if any(part.lower() in SKIP_FOLDERS for part in file.parts):
            continue
        if file.suffix.lower() not in CODE_EXTENSIONS:
            continue
        try:
            relative_path = file.relative_to(upload_path)
            files[str(relative_path)] = file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
    return files


def copy_non_code_files(upload_dir: str, output_dir: Path) -> int:
    """
    Copy all non-code files (views, static assets, config json etc.)
    from upload to output as-is. These are not touched by LLM.
    Returns count of files copied.
    """
    upload_path = Path(upload_dir)
    copied = 0
    for file in upload_path.rglob("*"):
        if not file.is_file():
            continue
        if any(part.lower() in SKIP_COPY_FOLDERS for part in file.parts):
            continue
        if file.suffix.lower() not in COPY_EXTENSIONS:
            continue
        try:
            rel = file.relative_to(upload_path)
            dst = output_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(str(file), str(dst))
            copied += 1
        except Exception:
            pass
    return copied

def extract_code(response: str, lang: str = "csharp") -> str:
    match = re.search(rf'```(?:{lang}|cs|xml|text)?\s*(.*?)\s*```', response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return response.strip()

def get_model_names(files: dict) -> list:
    """Extract class names from model files to build DbSet properties."""
    model_names = []
    for path, content in files.items():
        if "Models" in path and path.endswith(".cs"):
            match = re.search(r'public class (\w+)', content)
            if match:
                model_names.append(match.group(1))
    return model_names

def fix_application_context(content: str, model_names: list) -> str:
    """Replace any existing DbSet properties with correct ones based on actual model names."""
    if not model_names:
        return content
    dbsets = "\n".join([f"    public DbSet<{m}> {m}s {{ get; set; }}" for m in model_names])
    # Remove any existing DbSet lines first
    content = re.sub(r'\s*public DbSet<[^>]+>[^;]+;', '', content)
    # Inject correct DbSets after class opening brace
    content = re.sub(
        r'(public class ApplicationContext\s*:\s*DbContext\s*\{)',
        f'\\1\n{dbsets}\n',
        content
    )
    return content

def review_code(code: str, relative_path: str) -> str:
    """Single reviewer pass — catches what LLM missed. Only called for .cs files."""
    prompt = f"""Review this migrated .NET 8 C# file and fix any remaining issues.
File: {relative_path}

```csharp
{code[:8000]}
```"""
    try:
        reviewed = ask_with_system(SYSTEM_REVIEWER, prompt)
        return extract_code(reviewed, "csharp")
    except Exception:
        return code  # if reviewer fails, keep original migrated code


def find_program_and_startup(files: dict) -> tuple:
    """Find Program.cs and Startup.cs paths in the files dict."""
    program_path = next((k for k in files if k.replace('\\','/').endswith('Program.cs')), None)
    startup_path = next((k for k in files if k.replace('\\','/').endswith('Startup.cs')), None)
    return program_path, startup_path

def migrate(upload_dir: str, from_version: str, to_version: str, progress_callback: Optional[Callable[[str], None]] = None) -> dict:
    files = read_files_recursive(upload_dir)
    if not files:
        return {"success": False, "error": "No C# files found in upload directory"}

    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # Copy all non-code files first (views, static assets, wwwroot, json configs)
    if progress_callback:
        progress_callback("Copying views, static assets and config files...")
    copied = copy_non_code_files(upload_dir, output_dir)
    if progress_callback:
        progress_callback(f"Copied {copied} non-code file(s) to output.")

    migrated = {}
    total_files = len(files)
    model_names = get_model_names(files)

    # Find Program.cs and Startup.cs
    program_path, startup_path = find_program_and_startup(files)

    # Handle Program.cs + Startup.cs merge first
    if program_path and startup_path:
        if progress_callback:
            progress_callback(f"Merging Program.cs + Startup.cs into .NET 8 minimal hosting...")

        program_content = files[program_path]
        startup_content = files[startup_path]

        prompt = f"""Migrate these two files from {from_version} to .NET 8 minimal hosting.

--- Program.cs ---
{program_content[:4000]}

--- Startup.cs ---
{startup_content[:4000]}

Rules:
- Use WebApplication.CreateBuilder(args)
- Move ALL services from ConfigureServices into builder.Services — do not skip any
- Move ALL middleware from Configure into app pipeline in the same order
- Keep Swagger/OpenAPI setup if present (AddSwaggerGen, UseSwagger, UseSwaggerUI)
- Keep JWT authentication if present (AddAuthentication, AddJwtBearer)
- Keep CORS if present (AddCors, UseCors)
- Keep any custom services, repositories, or interfaces registered
- End with app.Run()
- NO Startup class, NO CreateHostBuilder, NO IHostBuilder
- Return ONLY the complete Program.cs inside a ```csharp block."""

        response = ask_with_system(SYSTEM_PROGRAM, prompt)
        merged_code = extract_code(response, "csharp")
        if progress_callback:
            progress_callback("Reviewing merged Program.cs...")
        merged_code = review_code(merged_code, program_path)
        time.sleep(1)

        out_path = output_dir / program_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(merged_code, encoding="utf-8")
        migrated[program_path] = merged_code
        migrated[startup_path] = "[merged into Program.cs]"

        if progress_callback:
            progress_callback(f"Merged Program.cs + Startup.cs (1/{total_files})")

    elif program_path and not startup_path:
        # Program.cs only — migrate it directly with SYSTEM_PROGRAM
        if progress_callback:
            progress_callback("Migrating Program.cs to .NET 8 minimal hosting...")

        program_content = files[program_path]
        prompt = f"""Migrate this Program.cs from {from_version} to .NET 8 minimal hosting.

{program_content[:4000]}

Rules:
- Use WebApplication.CreateBuilder(args)
- Keep ALL services and middleware intact
- Keep Swagger, JWT, CORS, Razor Pages, MVC — whatever is already there
- End with app.Run()
- Return ONLY the complete Program.cs inside a ```csharp block."""

        response = ask_with_system(SYSTEM_PROGRAM, prompt)
        program_code = extract_code(response, "csharp")
        if progress_callback:
            progress_callback("Reviewing Program.cs...")
        program_code = review_code(program_code, program_path)
        time.sleep(1)

        out_path = output_dir / program_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(program_code, encoding="utf-8")
        migrated[program_path] = program_code

        if progress_callback:
            progress_callback(f"Migrated Program.cs (1/{total_files})")

    for index, (relative_path, content) in enumerate(files.items(), start=1):
        # Skip Program.cs and Startup.cs — already handled
        if relative_path == program_path or relative_path == startup_path:
            continue

        # Skip obj/bin folders
        path_parts = Path(relative_path).parts
        if any(part.lower() in {"obj", "bin", ".vs", ".git"} for part in path_parts):
            continue

        if progress_callback:
            progress_callback(f"Migrating {relative_path} ({index}/{total_files})")

        file_type = Path(relative_path).suffix

        if file_type == '.cs':
            # Special fix for ApplicationContext — inject correct DbSets directly
            if 'ApplicationContext' in relative_path or 'ApplicationContext' in content:
                fixed = fix_application_context(content, model_names)
                prompt = f"""Migrate this C# DbContext file from {from_version} to .NET 8 / C# 12.
File: {relative_path}

```csharp
{fixed[:8000]}
```

Rules:
- Use file-scoped namespace
- Keep ALL DbSet properties exactly as they are in the input — do not remove or rename any
- Ensure constructor takes DbContextOptions
- Return ONLY the complete migrated C# code in a ```csharp block."""
            else:
                prompt = f"""Migrate this C# file from {from_version} to .NET 8 / C# 12.
File: {relative_path}

```csharp
{content[:8000]}
```

Return ONLY the complete migrated C# code in a ```csharp block."""
            response = ask_with_system(SYSTEM_CS, prompt)
            code = extract_code(response, "csharp")
            # Reviewer pass — catches what LLM missed
            if progress_callback:
                progress_callback(f"Reviewing {relative_path}...")
            code = review_code(code, relative_path)
            time.sleep(1)  # small gap between migrate + review calls

        elif file_type == '.csproj':
            prompt = f"""Migrate this .csproj from {from_version} to .NET 8.
File: {relative_path}

```xml
{content[:8000]}
```

Return ONLY the migrated XML in a ```xml block."""
            response = ask_with_system(SYSTEM_CSPROJ, prompt)
            code = extract_code(response, "xml")

        elif file_type == '.sln':
            # .sln already copied by copy_non_code_files — skip
            continue

        else:
            # all other files already copied — skip
            continue

        out_path = output_dir / relative_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(code, encoding="utf-8")
        migrated[relative_path] = code

        if progress_callback:
            progress_callback(f"Saved {relative_path} ({index}/{total_files})")

        # Small delay to avoid Groq rate limits
        time.sleep(2)

    return {"success": True, "migrated": migrated, "count": len(migrated), "output_dir": str(output_dir)}

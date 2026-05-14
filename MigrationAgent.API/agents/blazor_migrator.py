"""
Blazor Migration Agent — runs after Web Forms Agent, before Fix Agent.
Migrates .razor Blazor components to .NET 8.
Layer 1: Deterministic fixes — lifecycle methods, inject syntax, namespace updates.
Layer 2: LLM pass only for complex components that still have legacy patterns.
"""
from pathlib import Path
import re
from typing import Callable, Optional

SKIP_FOLDERS = {"obj", "bin", ".vs", ".git", "node_modules"}


# ── Layer 1: Deterministic fixes ─────────────────────────────────────────

def _fix_lifecycle_methods(content: str) -> str:

    # OnInitialized → OnInitializedAsync where async is needed
    content = re.sub(
        r'protected\s+override\s+void\s+OnInitialized\s*\(\s*\)',
        'protected override void OnInitialized()',
        content
    )

    # Task OnInitializedAsync — ensure correct override signature
    content = re.sub(
        r'protected\s+override\s+async\s+Task\s+OnInitializedAsync\s*\(\s*\)',
        'protected override async Task OnInitializedAsync()',
        content
    )

    # OnParametersSet → keep as-is (still valid in .NET 8)
    # OnAfterRender → keep as-is (still valid in .NET 8)

    # StateHasChanged() — still valid, no change needed
    # InvokeAsync(StateHasChanged) — still valid

    return content


def _fix_inject_syntax(content: str) -> str:

    # @inject ServiceType name — still valid in .NET 8, no change needed
    # But ensure proper using if HttpClient is injected
    if '@inject HttpClient' in content and 'using System.Net.Http' not in content:
        content = '@using System.Net.Http\n' + content

    return content


def _fix_namespace_directives(content: str) -> str:

    # @using Microsoft.AspNetCore.Components — still valid
    # Remove any @using that references old Blazor packages
    content = re.sub(
        r'@using\s+Microsoft\.AspNetCore\.Blazor\b[^\n]*\n?',
        '',
        content
    )

    # Replace old Blazor package references
    content = re.sub(
        r'@using\s+Microsoft\.AspNetCore\.Blazor\.Components\b[^\n]*\n?',
        '@using Microsoft.AspNetCore.Components\n',
        content
    )

    return content


def _fix_component_base(content: str) -> str:

    # BlazorComponent → ComponentBase
    content = re.sub(
        r'\bBlazorComponent\b',
        'ComponentBase',
        content
    )

    # IComponent → ComponentBase where used as base class
    content = re.sub(
        r':\s*IComponent\b',
        ': ComponentBase',
        content
    )

    return content


def _fix_event_callbacks(content: str) -> str:

    # Action → EventCallback for component parameters
    content = re.sub(
        r'\[Parameter\]\s*public\s+Action\s*(<[^>]+>)?\s+(\w+)\s*\{',
        lambda m: f'[Parameter] public EventCallback{m.group(1) or ""} {m.group(2)} {{',
        content
    )

    # Func<Task> → EventCallback for async handlers
    content = re.sub(
        r'\[Parameter\]\s*public\s+Func<Task>\s+(\w+)\s*\{',
        lambda m: f'[Parameter] public EventCallback {m.group(1)} {{',
        content
    )

    return content


def _fix_bind_syntax(content: str) -> str:

    # @bind-Value → @bind (simplified in .NET 8)
    content = re.sub(
        r'@bind-Value\s*=\s*"([^"]+)"',
        lambda m: f'@bind="{m.group(1)}"',
        content
    )

    # bind:event="oninput" → @bind:event="oninput" (correct syntax)
    content = re.sub(
        r'\bbind:event\b',
        '@bind:event',
        content
    )

    return content


def _fix_routing(content: str) -> str:

    # @page directive — still valid, no change needed
    # @layout directive — still valid

    # Old route constraints — fix if needed
    content = re.sub(
        r'@page\s+"([^"]*)\{(\w+):guid\}([^"]*)"',
        lambda m: f'@page "{m.group(1)}{{{m.group(2)}:guid}}{m.group(3)}"',
        content
    )

    return content


def _fix_js_interop(content: str) -> str:

    # IJSRuntime.InvokeAsync — still valid in .NET 8
    # JSRuntime.Current → inject IJSRuntime
    content = re.sub(
        r'\bJSRuntime\.Current\b',
        '_jsRuntime',
        content
    )

    return content


def _fix_http_client(content: str) -> str:

    # HttpClient.GetJsonAsync → HttpClient.GetFromJsonAsync (.NET 8)
    content = re.sub(
        r'\.GetJsonAsync\s*<([^>]+)>\s*\(',
        lambda m: f'.GetFromJsonAsync<{m.group(1)}>(',
        content
    )

    # HttpClient.PostJsonAsync → HttpClient.PostAsJsonAsync (.NET 8)
    content = re.sub(
        r'\.PostJsonAsync\s*\(',
        '.PostAsJsonAsync(',
        content
    )

    # HttpClient.PutJsonAsync → HttpClient.PutAsJsonAsync (.NET 8)
    content = re.sub(
        r'\.PutJsonAsync\s*\(',
        '.PutAsJsonAsync(',
        content
    )

    # Add using for System.Net.Http.Json if GetFromJsonAsync is used
    if 'GetFromJsonAsync' in content and 'using System.Net.Http.Json' not in content:
        content = '@using System.Net.Http.Json\n' + content

    return content


def _fix_razor_component(content: str) -> str:
    """Apply all deterministic fixes to a .razor file."""
    content = _fix_namespace_directives(content)
    content = _fix_component_base(content)
    content = _fix_lifecycle_methods(content)
    content = _fix_inject_syntax(content)
    content = _fix_event_callbacks(content)
    content = _fix_bind_syntax(content)
    content = _fix_routing(content)
    content = _fix_js_interop(content)
    content = _fix_http_client(content)
    return content


def _fix_program_cs_for_blazor(output_dir: Path) -> list:
    """Ensure Program.cs has correct Blazor setup for .NET 8."""
    fixes = []
    for program_cs in output_dir.rglob('Program.cs'):
        if any(p.lower() in SKIP_FOLDERS for p in program_cs.parts):
            continue
        try:
            content = program_cs.read_text(encoding='utf-8', errors='ignore')
            original = content

            # Blazor Server
            if 'AddServerSideBlazor' in content or 'MapBlazorHub' in content:
                if 'AddServerSideBlazor' not in content:
                    content = content.replace(
                        'var app = builder.Build()',
                        'builder.Services.AddServerSideBlazor();\n\nvar app = builder.Build()',
                        1
                    )
                    fixes.append('Added AddServerSideBlazor() to Program.cs')
                if 'MapBlazorHub' not in content:
                    content = content.replace(
                        'app.Run()',
                        'app.MapBlazorHub();\napp.MapFallbackToPage("/_Host");\n\napp.Run()',
                        1
                    )
                    fixes.append('Added MapBlazorHub() to Program.cs')

            # Blazor WebAssembly
            if 'AddBlazorWebAssembly' in content or 'WebAssemblyHostBuilder' in content:
                fixes.append('Blazor WebAssembly detected — Program.cs preserved as-is')

            if content != original:
                program_cs.write_text(content, encoding='utf-8')
        except Exception:
            pass
    return fixes


def _ensure_imports_razor(output_dir: Path) -> list:
    """Ensure _Imports.razor has correct using statements for .NET 8."""
    fixes = []
    required_usings = [
        '@using System.Net.Http',
        '@using Microsoft.AspNetCore.Components',
        '@using Microsoft.AspNetCore.Components.Forms',
        '@using Microsoft.AspNetCore.Components.Routing',
        '@using Microsoft.AspNetCore.Components.Web',
        '@using Microsoft.JSInterop',
    ]

    for imports_razor in output_dir.rglob('_Imports.razor'):
        if any(p.lower() in SKIP_FOLDERS for p in imports_razor.parts):
            continue
        try:
            content = imports_razor.read_text(encoding='utf-8', errors='ignore')
            original = content
            for using in required_usings:
                ns = using.replace('@using ', '')
                if ns not in content:
                    content = content + f'\n{using}'
            if content != original:
                imports_razor.write_text(content, encoding='utf-8')
                fixes.append('Updated _Imports.razor with .NET 8 using statements')
        except Exception:
            pass

    return fixes


# ── Layer 2: LLM pass ─────────────────────────────────────────────────────

def _needs_llm_pass(content: str) -> bool:
    """Check if component still has legacy patterns after deterministic pass."""
    legacy_patterns = [
        r'BlazorComponent',
        r'Microsoft\.AspNetCore\.Blazor\b',
        r'JSRuntime\.Current',
        r'\.GetJsonAsync',
        r'\.PostJsonAsync',
    ]
    return any(re.search(p, content) for p in legacy_patterns)


def _llm_migrate_blazor(path: str, content: str) -> str:
    """Send complex Blazor component to LLM for targeted rewrite."""
    try:
        from agents.llm import ask_with_system
        system = """You are a .NET 8 Blazor migration expert.
Migrate legacy Blazor components to .NET 8.
Rules:
- Replace BlazorComponent with ComponentBase
- Replace GetJsonAsync with GetFromJsonAsync
- Replace PostJsonAsync with PostAsJsonAsync
- Fix any deprecated lifecycle methods
- Keep all component logic, parameters, and UI intact
- Return ONLY the migrated .razor content. Nothing else."""

        prompt = f"""Migrate this Blazor component to .NET 8.
File: {path}

{content[:6000]}

Return ONLY the migrated .razor content."""

        result = ask_with_system(system, prompt)
        result = re.sub(r'^```(?:razor|html|cshtml)?\s*', '', result, flags=re.MULTILINE)
        result = re.sub(r'\s*```$', '', result, flags=re.MULTILINE)
        return result.strip()
    except Exception:
        return content


# ── Main entry point ──────────────────────────────────────────────────────

def run_blazor_migrator(
    output_dir: str,
    from_version: str,
    to_version: str,
    progress_callback: Optional[Callable[[str], None]] = None
) -> dict:
    """
    Main entry point called from migration pipeline.
    Only runs if .razor files exist in the output.
    Returns full result for reporter.
    """
    out = Path(output_dir)

    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    # Find all .razor files
    razor_files = [
        f for f in out.rglob('*.razor')
        if not any(p.lower() in SKIP_FOLDERS for p in f.parts)
    ]

    if not razor_files:
        return {
            'skipped': True,
            'reason': 'No .razor files found — project has no Blazor components',
            'components_processed': 0,
            'fixes_applied': 0,
            'llm_passes': 0,
            'manual_review': [],
            'structural_fixes': [],
            'changes': [],
        }

    progress(f'Blazor Agent: Found {len(razor_files)} .razor file(s) — starting migration...')

    total_fixes = 0
    llm_passes = 0
    manual_review = []
    changes = []

    for razor_file in razor_files:
        rel = str(razor_file.relative_to(out))
        try:
            original = razor_file.read_text(encoding='utf-8', errors='ignore')

            # Layer 1 — deterministic
            migrated = _fix_razor_component(original)

            file_fixes = sum([
                migrated.count('ComponentBase') - original.count('ComponentBase'),
                migrated.count('GetFromJsonAsync') - original.count('GetFromJsonAsync'),
                migrated.count('PostAsJsonAsync') - original.count('PostAsJsonAsync'),
                migrated.count('EventCallback') - original.count('EventCallback'),
            ])
            file_fixes = max(0, file_fixes)
            total_fixes += file_fixes

            # Layer 2 — LLM pass only if legacy patterns remain
            if _needs_llm_pass(migrated):
                progress(f'Blazor Agent: LLM pass on {razor_file.name}...')
                migrated = _llm_migrate_blazor(rel, migrated)
                llm_passes += 1
                if _needs_llm_pass(migrated):
                    manual_review.append(f'{rel}: Legacy patterns remain — manual review required')

            if migrated != original:
                razor_file.write_text(migrated, encoding='utf-8')
                changes.append(f'Migrated {rel} — {file_fixes} fix(es) applied')
                progress(f'Blazor Agent: Migrated {razor_file.name}')

        except Exception as e:
            manual_review.append(f'{rel}: Error during migration — {str(e)}')

    # Structural fixes
    structural_fixes = []
    structural_fixes.extend(_fix_program_cs_for_blazor(out))
    structural_fixes.extend(_ensure_imports_razor(out))
    if structural_fixes:
        changes.extend(structural_fixes)

    progress(f'Blazor Agent: {len(razor_files)} component(s) processed, {total_fixes} fix(es) applied.')

    return {
        'skipped': False,
        'reason': '',
        'components_processed': len(razor_files),
        'fixes_applied': total_fixes,
        'llm_passes': llm_passes,
        'manual_review': manual_review,
        'structural_fixes': structural_fixes,
        'changes': changes,
    }

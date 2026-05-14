"""
Web Forms Migration Agent — runs after View Migration Agent, before Fix Agent.
Migrates .aspx/.ascx/.master Web Forms files to .NET 8 Razor Pages.
Layer 1: Deterministic regex — asp:* controls → HTML/Tag Helpers.
Layer 2: Code-behind .aspx.cs → Razor Page .cshtml.cs restructure.
Layer 3: LLM pass only for complex event handlers that regex cannot handle.
"""
from pathlib import Path
import re
from typing import Callable, Optional

SKIP_FOLDERS = {"obj", "bin", ".vs", ".git", "node_modules"}

# ── Layer 1: Deterministic asp:* → HTML/Tag Helper replacements ──────────

def _replace_asp_controls(content: str) -> str:

    # Remove runat="server" from all tags
    content = re.sub(r'\s*runat\s*=\s*"server"', '', content)

    # <asp:TextBox ID="x" /> → <input asp-for="x" class="form-control">
    content = re.sub(
        r'<asp:TextBox[^>]*ID\s*=\s*"(\w+)"[^>]*/?>',
        lambda m: f'<input asp-for="{m.group(1)}" class="form-control">',
        content, flags=re.IGNORECASE
    )

    # <asp:Password ID="x" /> → <input asp-for="x" type="password" class="form-control">
    content = re.sub(
        r'<asp:Password[^>]*ID\s*=\s*"(\w+)"[^>]*/?>',
        lambda m: f'<input asp-for="{m.group(1)}" type="password" class="form-control">',
        content, flags=re.IGNORECASE
    )

    # <asp:TextArea ID="x" /> → <textarea asp-for="x" class="form-control"></textarea>
    content = re.sub(
        r'<asp:TextArea[^>]*ID\s*=\s*"(\w+)"[^>]*/?>',
        lambda m: f'<textarea asp-for="{m.group(1)}" class="form-control"></textarea>',
        content, flags=re.IGNORECASE
    )

    # <asp:Label ID="x" Text="y" /> → <label asp-for="x">y</label>
    content = re.sub(
        r'<asp:Label[^>]*ID\s*=\s*"(\w+)"[^>]*Text\s*=\s*"([^"]*)"[^>]*/?>',
        lambda m: f'<label asp-for="{m.group(1)}">{m.group(2)}</label>',
        content, flags=re.IGNORECASE
    )
    content = re.sub(
        r'<asp:Label[^>]*Text\s*=\s*"([^"]*)"[^>]*ID\s*=\s*"(\w+)"[^>]*/?>',
        lambda m: f'<label asp-for="{m.group(2)}">{m.group(1)}</label>',
        content, flags=re.IGNORECASE
    )

    # <asp:Button ID="x" Text="y" /> → <button type="submit">y</button>
    content = re.sub(
        r'<asp:Button[^>]*Text\s*=\s*"([^"]*)"[^>]*/?>',
        lambda m: f'<button type="submit" class="btn btn-primary">{m.group(1)}</button>',
        content, flags=re.IGNORECASE
    )

    # <asp:LinkButton ID="x" Text="y" /> → <a asp-action="x">y</a>
    content = re.sub(
        r'<asp:LinkButton[^>]*ID\s*=\s*"(\w+)"[^>]*Text\s*=\s*"([^"]*)"[^>]*/?>',
        lambda m: f'<a asp-action="{m.group(1)}">{m.group(2)}</a>',
        content, flags=re.IGNORECASE
    )

    # <asp:HyperLink NavigateUrl="x" Text="y" /> → <a href="x">y</a>
    content = re.sub(
        r'<asp:HyperLink[^>]*NavigateUrl\s*=\s*"([^"]*)"[^>]*Text\s*=\s*"([^"]*)"[^>]*/?>',
        lambda m: f'<a href="{m.group(1)}">{m.group(2)}</a>',
        content, flags=re.IGNORECASE
    )

    # <asp:CheckBox ID="x" /> → <input asp-for="x" type="checkbox">
    content = re.sub(
        r'<asp:CheckBox[^>]*ID\s*=\s*"(\w+)"[^>]*/?>',
        lambda m: f'<input asp-for="{m.group(1)}" type="checkbox">',
        content, flags=re.IGNORECASE
    )

    # <asp:RadioButton ID="x" /> → <input asp-for="x" type="radio">
    content = re.sub(
        r'<asp:RadioButton[^>]*ID\s*=\s*"(\w+)"[^>]*/?>',
        lambda m: f'<input asp-for="{m.group(1)}" type="radio">',
        content, flags=re.IGNORECASE
    )

    # <asp:DropDownList ID="x"> → <select asp-for="x" asp-items="Model.xList">
    content = re.sub(
        r'<asp:DropDownList[^>]*ID\s*=\s*"(\w+)"[^>]*>.*?</asp:DropDownList>',
        lambda m: f'<select asp-for="{m.group(1)}" asp-items="Model.{m.group(1)}List" class="form-control"></select>',
        content, flags=re.IGNORECASE | re.DOTALL
    )

    # <asp:ListBox ID="x"> → <select asp-for="x" multiple>
    content = re.sub(
        r'<asp:ListBox[^>]*ID\s*=\s*"(\w+)"[^>]*>.*?</asp:ListBox>',
        lambda m: f'<select asp-for="{m.group(1)}" multiple class="form-control"></select>',
        content, flags=re.IGNORECASE | re.DOTALL
    )

    # <asp:HiddenField ID="x" /> → <input asp-for="x" type="hidden">
    content = re.sub(
        r'<asp:HiddenField[^>]*ID\s*=\s*"(\w+)"[^>]*/?>',
        lambda m: f'<input asp-for="{m.group(1)}" type="hidden">',
        content, flags=re.IGNORECASE
    )

    # <asp:Image ImageUrl="x" AlternateText="y" /> → <img src="x" alt="y">
    content = re.sub(
        r'<asp:Image[^>]*ImageUrl\s*=\s*"([^"]*)"[^>]*AlternateText\s*=\s*"([^"]*)"[^>]*/?>',
        lambda m: f'<img src="{m.group(1)}" alt="{m.group(2)}">',
        content, flags=re.IGNORECASE
    )

    # <asp:Literal ID="x" Text="y" /> → y
    content = re.sub(
        r'<asp:Literal[^>]*Text\s*=\s*"([^"]*)"[^>]*/?>',
        lambda m: m.group(1),
        content, flags=re.IGNORECASE
    )

    # <asp:Panel ID="x"> → <div id="x">
    content = re.sub(
        r'<asp:Panel[^>]*ID\s*=\s*"(\w+)"[^>]*>',
        lambda m: f'<div id="{m.group(1)}">',
        content, flags=re.IGNORECASE
    )
    content = re.sub(r'</asp:Panel>', '</div>', content, flags=re.IGNORECASE)

    # <asp:PlaceHolder> → <div>
    content = re.sub(r'<asp:PlaceHolder[^>]*>', '<div>', content, flags=re.IGNORECASE)
    content = re.sub(r'</asp:PlaceHolder>', '</div>', content, flags=re.IGNORECASE)

    # <asp:ValidationSummary /> → <div asp-validation-summary="All"></div>
    content = re.sub(
        r'<asp:ValidationSummary[^>]*/?>',
        '<div asp-validation-summary="All" class="text-danger"></div>',
        content, flags=re.IGNORECASE
    )

    # <asp:RequiredFieldValidator ControlToValidate="x" ErrorMessage="y" />
    content = re.sub(
        r'<asp:RequiredFieldValidator[^>]*ControlToValidate\s*=\s*"(\w+)"[^>]*ErrorMessage\s*=\s*"([^"]*)"[^>]*/?>',
        lambda m: f'<span asp-validation-for="{m.group(1)}" class="text-danger">{m.group(2)}</span>',
        content, flags=re.IGNORECASE
    )

    # <asp:GridView> → @foreach table scaffold
    content = re.sub(
        r'<asp:GridView[^>]*ID\s*=\s*"(\w+)"[^>]*>.*?</asp:GridView>',
        lambda m: (
            f'<table class="table">\n'
            f'  <thead><tr><!-- TODO: add column headers --></tr></thead>\n'
            f'  <tbody>\n'
            f'    @foreach (var item in Model.{m.group(1)})\n'
            f'    {{\n'
            f'      <tr><!-- TODO: add columns --></tr>\n'
            f'    }}\n'
            f'  </tbody>\n'
            f'</table>'
        ),
        content, flags=re.IGNORECASE | re.DOTALL
    )

    # <asp:Repeater> → @foreach scaffold
    content = re.sub(
        r'<asp:Repeater[^>]*ID\s*=\s*"(\w+)"[^>]*>.*?</asp:Repeater>',
        lambda m: (
            f'@foreach (var item in Model.{m.group(1)})\n'
            f'{{\n'
            f'  <!-- TODO: add repeater item template -->\n'
            f'}}'
        ),
        content, flags=re.IGNORECASE | re.DOTALL
    )

    # <asp:ScriptManager> → remove (not needed in .NET 8)
    content = re.sub(
        r'<asp:ScriptManager[^>]*/?>',
        '',
        content, flags=re.IGNORECASE
    )

    # <asp:UpdatePanel> → remove wrapper, keep content
    content = re.sub(r'<asp:UpdatePanel[^>]*>', '', content, flags=re.IGNORECASE)
    content = re.sub(r'</asp:UpdatePanel>', '', content, flags=re.IGNORECASE)
    content = re.sub(r'<asp:ContentTemplate[^>]*>', '', content, flags=re.IGNORECASE)
    content = re.sub(r'</asp:ContentTemplate>', '', content, flags=re.IGNORECASE)

    # <asp:Content ContentPlaceHolderID="x"> → @section x {
    content = re.sub(
        r'<asp:Content[^>]*ContentPlaceHolderID\s*=\s*"(\w+)"[^>]*>',
        lambda m: f'@section {m.group(1)} {{',
        content, flags=re.IGNORECASE
    )
    content = re.sub(r'</asp:Content>', '}', content, flags=re.IGNORECASE)

    # Remove <%@ Page ... %> directive
    content = re.sub(r'<%@\s*Page[^%]*%>', '', content, flags=re.IGNORECASE)

    # Remove <%@ Control ... %> directive
    content = re.sub(r'<%@\s*Control[^%]*%>', '', content, flags=re.IGNORECASE)

    # Remove <%@ Master ... %> directive
    content = re.sub(r'<%@\s*Master[^%]*%>', '', content, flags=re.IGNORECASE)

    # Remove <%@ Register ... %> directives
    content = re.sub(r'<%@\s*Register[^%]*%>', '', content, flags=re.IGNORECASE)

    # Remove <%@ Import ... %> directives
    content = re.sub(r'<%@\s*Import[^%]*%>', '', content, flags=re.IGNORECASE)

    # <% ... %> inline code blocks → @{ ... }
    content = re.sub(r'<%\s*(.*?)\s*%>', lambda m: f'@{{ {m.group(1)} }}', content, flags=re.DOTALL)

    # <%= ... %> expression → @(...)
    content = re.sub(r'<%=\s*(.*?)\s*%>', lambda m: f'@({m.group(1)})', content, flags=re.DOTALL)

    # Remove any remaining <asp:*> tags that weren't caught
    content = re.sub(r'<asp:\w+[^>]*/>', '', content, flags=re.IGNORECASE)
    content = re.sub(r'<asp:\w+[^>]*>', '', content, flags=re.IGNORECASE)
    content = re.sub(r'</asp:\w+>', '', content, flags=re.IGNORECASE)

    return content


def _convert_master_to_layout(content: str, filename: str) -> str:
    """Convert .master file to _Layout.cshtml."""
    # Remove master page directive
    content = re.sub(r'<%@\s*Master[^%]*%>', '', content, flags=re.IGNORECASE)

    # ContentPlaceHolder → @RenderBody() or @RenderSection()
    content = re.sub(
        r'<asp:ContentPlaceHolder[^>]*ID\s*=\s*"MainContent"[^>]*>.*?</asp:ContentPlaceHolder>',
        '@RenderBody()',
        content, flags=re.IGNORECASE | re.DOTALL
    )
    content = re.sub(
        r'<asp:ContentPlaceHolder[^>]*ID\s*=\s*"(\w+)"[^>]*>.*?</asp:ContentPlaceHolder>',
        lambda m: f'@RenderSection("{m.group(1)}", required: false)',
        content, flags=re.IGNORECASE | re.DOTALL
    )

    # Add layout header if not present
    if '<!DOCTYPE' in content and '@{' not in content:
        content = '@{\n    Layout = null;\n}\n' + content

    # Remove runat="server"
    content = re.sub(r'\s*runat\s*=\s*"server"', '', content)

    return content


def _convert_codebehind_to_razorpage(content: str, page_name: str) -> str:
    """Convert .aspx.cs code-behind to Razor Page .cshtml.cs structure."""
    # Extract namespace
    ns_match = re.search(r'namespace\s+([\w\.]+)', content)
    namespace = ns_match.group(1) if ns_match else 'MyApp.Pages'

    # Extract class name
    class_match = re.search(r'public\s+(?:partial\s+)?class\s+(\w+)', content)
    class_name = class_match.group(1) if class_match else page_name

    # Extract Page_Load → OnGet
    page_load = ''
    load_match = re.search(
        r'protected\s+void\s+Page_Load\s*\([^)]*\)\s*\{(.*?)\n\s*\}',
        content, re.DOTALL
    )
    if load_match:
        page_load = load_match.group(1)

    # Extract Button click handlers → OnPost methods
    post_handlers = []
    for m in re.finditer(
        r'protected\s+void\s+(\w+)_Click\s*\([^)]*\)\s*\{(.*?)\n\s*\}',
        content, re.DOTALL
    ):
        post_handlers.append((m.group(1), m.group(2)))

    # Build Razor Page model
    post_methods = '\n'.join([
        f'    public IActionResult OnPost{name}()\n    {{\n{body}\n        return Page();\n    }}'
        for name, body in post_handlers
    ])

    razor_page = f"""using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace {namespace};

public class {class_name}Model : PageModel
{{
    public void OnGet()
    {{{page_load}
    }}

{post_methods}
}}
"""
    return razor_page


# ── Layer 2: Check if LLM pass needed ────────────────────────────────────

def _needs_llm_pass(content: str) -> bool:
    """Check if file still has asp: controls or code blocks after deterministic pass."""
    return bool(re.search(r'<asp:\w+|<%[^@]', content))


def _llm_migrate_webforms(path: str, content: str) -> str:
    """Send complex Web Forms file to LLM for targeted rewrite."""
    try:
        from agents.llm import ask_with_system
        system = """You are a .NET 8 Razor Pages migration expert.
Convert legacy ASP.NET Web Forms to .NET 8 Razor Pages.
Rules:
- Replace ALL <asp:*> controls with equivalent HTML and Tag Helpers
- Replace code-behind event handlers with Razor Page OnGet/OnPost methods
- Keep all HTML structure, CSS classes, and layout intact
- Remove all runat="server" attributes
- Remove all <%@ ... %> directives
- Return ONLY the migrated content. Nothing else."""

        prompt = f"""Migrate this Web Forms file to .NET 8 Razor Pages.
File: {path}

{content[:6000]}

Return ONLY the migrated content."""

        result = ask_with_system(system, prompt)
        result = re.sub(r'^```(?:cshtml|html|razor|csharp)?\s*', '', result, flags=re.MULTILINE)
        result = re.sub(r'\s*```$', '', result, flags=re.MULTILINE)
        return result.strip()
    except Exception:
        return content


# ── Structural fixes ──────────────────────────────────────────────────────

def _ensure_razor_pages_setup(output_dir: Path) -> list:
    """Ensure Program.cs has AddRazorPages and MapRazorPages registered."""
    fixes = []
    for program_cs in output_dir.rglob('Program.cs'):
        if any(p.lower() in SKIP_FOLDERS for p in program_cs.parts):
            continue
        try:
            content = program_cs.read_text(encoding='utf-8', errors='ignore')
            original = content
            if 'AddRazorPages' not in content:
                content = content.replace(
                    'var app = builder.Build()',
                    'builder.Services.AddRazorPages();\n\nvar app = builder.Build()',
                    1
                )
                fixes.append('Added AddRazorPages() to Program.cs')
            if 'MapRazorPages' not in content:
                content = content.replace(
                    'app.Run()',
                    'app.MapRazorPages();\n\napp.Run()',
                    1
                )
                fixes.append('Added MapRazorPages() to Program.cs')
            if content != original:
                program_cs.write_text(content, encoding='utf-8')
        except Exception:
            pass
    return fixes


def _create_viewimports(output_dir: Path) -> list:
    """Ensure _ViewImports.cshtml exists with Tag Helper import."""
    fixes = []
    tag_helper_import = '@addTagHelper *, Microsoft.AspNetCore.Mvc.TagHelpers'
    for folder_name in ['Pages', 'Views']:
        for folder in output_dir.rglob(folder_name):
            if folder.is_dir() and not any(p.lower() in SKIP_FOLDERS for p in folder.parts):
                vi_path = folder / '_ViewImports.cshtml'
                if not vi_path.exists():
                    vi_path.write_text(
                        f'{tag_helper_import}\n@using Microsoft.AspNetCore.Mvc.Rendering\n',
                        encoding='utf-8'
                    )
                    fixes.append(f'Created _ViewImports.cshtml in {folder_name}/')
                break
    return fixes


# ── Main entry point ──────────────────────────────────────────────────────

def run_webforms_migrator(
    output_dir: str,
    from_version: str,
    to_version: str,
    progress_callback: Optional[Callable[[str], None]] = None
) -> dict:
    """
    Main entry point called from migration pipeline.
    Only runs if .aspx/.ascx/.master files exist in the output.
    Returns full result for reporter.
    """
    out = Path(output_dir)

    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    # Find all Web Forms files
    webforms_files = [
        f for f in out.rglob('*')
        if f.suffix.lower() in {'.aspx', '.ascx', '.master'}
        and not any(p.lower() in SKIP_FOLDERS for p in f.parts)
    ]

    if not webforms_files:
        return {
            'skipped': True,
            'reason': 'No Web Forms files found — project has no .aspx/.ascx/.master files',
            'pages_processed': 0,
            'controls_replaced': 0,
            'llm_passes': 0,
            'manual_review': [],
            'structural_fixes': [],
            'changes': [],
        }

    progress(f'Web Forms Agent: Found {len(webforms_files)} file(s) — starting migration...')

    total_controls_replaced = 0
    llm_passes = 0
    manual_review = []
    changes = []

    for wf_file in webforms_files:
        rel = str(wf_file.relative_to(out))
        try:
            original = wf_file.read_text(encoding='utf-8', errors='ignore')

            # Count asp: controls before
            controls_before = len(re.findall(r'<asp:\w+', original, re.IGNORECASE))

            if wf_file.suffix.lower() == '.master':
                # Convert master page to _Layout.cshtml
                migrated = _convert_master_to_layout(original, wf_file.stem)
                new_path = wf_file.with_name('_Layout.cshtml')
                migrated = _replace_asp_controls(migrated)
            else:
                # Layer 1 — deterministic
                migrated = _replace_asp_controls(original)

            controls_after = len(re.findall(r'<asp:\w+', migrated, re.IGNORECASE))
            replaced = controls_before - controls_after
            total_controls_replaced += replaced

            # Layer 2 — LLM pass only if asp: controls remain
            if _needs_llm_pass(migrated):
                progress(f'Web Forms Agent: LLM pass on {wf_file.name} ({controls_after} controls remaining)...')
                migrated = _llm_migrate_webforms(rel, migrated)
                llm_passes += 1
                remaining = len(re.findall(r'<asp:\w+', migrated, re.IGNORECASE))
                if remaining > 0:
                    manual_review.append(f'{rel}: {remaining} control(s) could not be auto-migrated — manual review required')

            # Rename .aspx → .cshtml, .ascx → .cshtml, .master → _Layout.cshtml
            if wf_file.suffix.lower() == '.master':
                new_file = wf_file.with_name('_Layout.cshtml')
            else:
                new_file = wf_file.with_suffix('.cshtml')

            new_file.write_text(migrated, encoding='utf-8')

            # Also handle code-behind file if exists
            codebehind = Path(str(wf_file) + '.cs')
            if codebehind.exists():
                cb_content = codebehind.read_text(encoding='utf-8', errors='ignore')
                razor_model = _convert_codebehind_to_razorpage(cb_content, wf_file.stem)
                new_codebehind = new_file.with_suffix('.cshtml.cs')
                new_codebehind.write_text(razor_model, encoding='utf-8')
                codebehind.unlink()
                changes.append(f'Converted code-behind {codebehind.name} → {new_codebehind.name}')

            # Remove original Web Forms file
            wf_file.unlink()

            changes.append(f'Migrated {rel} → {new_file.name} — {replaced} control(s) replaced')
            progress(f'Web Forms Agent: Migrated {wf_file.name} → {new_file.name}')

        except Exception as e:
            manual_review.append(f'{rel}: Error during migration — {str(e)}')

    # Structural fixes
    structural_fixes = []
    structural_fixes.extend(_ensure_razor_pages_setup(out))
    structural_fixes.extend(_create_viewimports(out))
    if structural_fixes:
        changes.extend(structural_fixes)

    progress(f'Web Forms Agent: {len(webforms_files)} file(s) migrated, {total_controls_replaced} control(s) replaced.')

    return {
        'skipped': False,
        'reason': '',
        'pages_processed': len(webforms_files),
        'controls_replaced': total_controls_replaced,
        'llm_passes': llm_passes,
        'manual_review': manual_review,
        'structural_fixes': structural_fixes,
        'changes': changes,
    }

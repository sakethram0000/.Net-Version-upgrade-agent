import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const API_BASE = import.meta.env.VITE_API_URL || '';
const steps = ['Ingest', 'Analyze', 'Transform', 'Validate', 'Package'];

function App() {
  const [fromVersion, setFromVersion] = useState('.NET Framework 4.8');
  const [toVersion, setToVersion] = useState('.NET 8');
  const [files, setFiles] = useState([]);
  const [githubUrl, setGithubUrl] = useState('');
  const [uploadMode, setUploadMode] = useState('local');
  const [inventory, setInventory] = useState(null);
  const [job, setJob] = useState(null);
  const [report, setReport] = useState(null);
  const [terminal, setTerminal] = useState('Terminal output will appear here when migration runs...');
  const [busy, setBusy] = useState('');
  const [runtime, setRuntime] = useState(null);
  const [selectedOutput, setSelectedOutput] = useState(null);
  const [appRuntime, setAppRuntime] = useState({ status: 'stopped', url: '', logs: [] });
  const [smokeTest, setSmokeTest] = useState(null);
  const [ollamaStatus, setOllamaStatus] = useState(null);
  const inputRef = useRef(null);

  useEffect(() => {
    fetchJson('/api/ollama/status')
      .then((data) => setOllamaStatus(data))
      .catch(() => setOllamaStatus({ connected: false, status: 'unreachable' }));
  }, []);

  const scopes = useMemo(() => [
    { label: 'Projects',   key: 'project_count',      value: () => inventory?.project_count || 0 },
    { label: 'CS Files',   key: 'source_file_count',  value: () => inventory?.source_file_count || 0 },
    { label: 'Packages',   key: 'packages',           value: () => inventory?.packages?.length || 0 },
    { label: 'Findings',   key: 'patterns',           value: () => inventory?.patterns?.length || 0 },
    { label: 'Complexity', key: 'complexity',         value: () => inventory?.complexity?.level || '—' },
    { label: 'Frameworks', key: 'frameworks',         value: () => inventory?.frameworks?.join(', ') || '—' },
  ], [inventory]);
  const stageIndex = getStageIndex(job);

  async function uploadSelected(event) {
    const selected = [...(event.target.files || [])];
    event.target.value = '';
    if (!selected.length) return;
    const form = new FormData();
    selected.forEach((file) => form.append('files', file));
    setBusy('upload');
    try {
      const data = await postForm('/api/files/upload', form);
      setFiles(data.files || []);
      log(`Uploaded ${data.files?.length || 0} file(s).`);
      await runAnalyze();
    } catch (err) {
      log(`Upload failed: ${err.message}`);
    } finally {
      setBusy('');
    }
  }

  async function fetchGithub() {
    if (!githubUrl.trim()) return;
    setBusy('github');
    try {
      const data = await postJson('/api/files/upload-github', { url: githubUrl });
      setFiles([{ name: data.repo, type: 'github', size: data.total_files || 0 }]);
      log(`Fetched ${data.repo} from ${data.branch}.`);
      await runAnalyze();
    } catch (err) {
      log(`GitHub fetch failed: ${err.message}`);
    } finally {
      setBusy('');
    }
  }

  async function runAnalyze() {
    setBusy('analyze');
    try {
      const data = await postJson('/api/migration/analyze', { from_version: fromVersion, to_version: toVersion });
      setInventory(data);
      log(`Analyzed ${data.project_count} project(s), ${data.source_file_count} C# file(s).`);
    } catch (err) {
      log(`Analysis failed: ${err.message}`);
    } finally {
      setBusy('');
    }
  }

  async function startMigration() {
    setBusy('migration');
    setReport(null);
    setSelectedOutput(null);
    try {
      const data = await postJson('/api/migration/migrate', {
        from_version: fromVersion,
        to_version: toVersion,
      });
      log(`Migration job queued: ${data.job_id}`);
      poll(data.job_id);
    } catch (err) {
      log(`Migration failed to start: ${err.message}`);
      setBusy('');
    }
  }

  async function poll(jobId) {
    let lastProgress = '';
    for (let i = 0; i < 360; i += 1) {
      const data = await fetchJson(`/api/migration/status/${jobId}`);
      setJob(data);
      const msg = `${data.stage}: ${data.progress}`;
      if (msg !== lastProgress) {
        log(msg);
        lastProgress = msg;
      }
      if (['completed', 'needs_review', 'failed'].includes(data.status)) {
        setBusy('');
        if (data.status === 'failed') {
          log(`Migration failed: ${data.error || 'Unknown error'}`);
        } else {
          try {
            setReport(await fetchJson('/api/migration/report'));
          } catch {
            setReport(data.result || null);
          }
        }
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    setBusy('');
    log('Migration polling timed out.');
  }

  async function loadRuntime() {
    const data = await fetchJson('/health');
    setRuntime(data.runtime ?? null);
  }

  async function startMigratedApp() {
    if (!job?.job_id) return;
    setBusy('runtime');
    try {
      const data = await fetchJson(`/api/migration/run/${job.job_id}`, { method: 'POST' });
      setAppRuntime(data);
      log(`Runtime: ${data.status} ${data.url || ''}`);
    } catch (err) {
      log(`Runtime start failed: ${err.message}`);
    } finally {
      setBusy('');
    }
  }

  async function stopMigratedApp() {
    if (!job?.job_id) return;
    try {
      const data = await fetchJson(`/api/migration/run/${job.job_id}/stop`, { method: 'POST' });
      setAppRuntime(data);
      log('Migrated app stopped.');
    } catch (err) {
      log(`Stop failed: ${err.message}`);
    }
  }

  async function refreshMigratedApp() {
    if (!job?.job_id) return;
    try {
      const data = await fetchJson(`/api/migration/run/${job.job_id}`);
      setAppRuntime(data);
    } catch (err) {
      log(`Refresh failed: ${err.message}`);
    }
  }

  async function runSmokeTest() {
    if (!job?.job_id) return;
    setBusy('smoke');
    try {
      const data = await fetchJson(`/api/migration/run/${job.job_id}/smoke`, { method: 'POST' });
      setSmokeTest(data);
      setAppRuntime(data.runtime || appRuntime);
      log(`Smoke test ${data.status}: ${data.summary}`);
    } catch (err) {
      log(`Smoke test failed: ${err.message}`);
    } finally {
      setBusy('');
    }
  }

  function log(line) {
    setTerminal((prev) => `${prev === 'Terminal output will appear here when migration runs...' ? '' : `${prev}\n`}${line}`);
  }

  return (
    <>
      <section className="ma-hero">
        <div className="ma-hero-content">
          <div className="ma-hero-brand">
            <div className="ma-hero-logo">.N</div>
            <div>
              <div className="ma-hero-title">.NET Migration Agent</div>
              <div className="ma-hero-subtitle">Migrate legacy .NET applications to modern target versions with Microsoft Agent Framework orchestration and LLM-assisted build fixing.</div>
            </div>
          </div>
          <div className="ma-hero-pills">
            <span className="hero-pill"><span className="pill-dot blue"></span>Microsoft Agent Framework</span>
            <span className="hero-pill"><span className="pill-dot purple"></span>Groq LLM</span>
            <span className="hero-pill"><span className="pill-dot green"></span>.NET 8/9/10</span>
            <span className="hero-pill"><span className="pill-dot orange"></span>Build Validation</span>
            <OllamaStatus status={ollamaStatus} />
          </div>
        </div>
      </section>

      <main className="ma-container">
        <section className="ma-card">
          <div className="ma-card-header"><div className="ma-card-title">Migration Path</div><button onClick={loadRuntime}>Runtime Status</button></div>
          <div className="ma-card-body">
            <div className="version-row">
              <Select label="From Version" value={fromVersion} setValue={setFromVersion} values={['.NET Framework 4.5', '.NET Framework 4.6', '.NET Framework 4.7', '.NET Framework 4.8', '.NET Core 3.1', '.NET 5', '.NET 6', '.NET 7']} />
              <div className="version-arrow">to</div>
              <Select label="To Version" value={toVersion} setValue={setToVersion} values={['.NET 8', '.NET 9', '.NET 10']} />
            </div>
            <div className={`compat-matrix ${inventory?.complexity?.level === 'High' ? 'warning' : 'success'}`}>
              <div className="compat-left">
                <div className="compat-status-title">{inventory ? `${inventory.complexity.level} migration complexity` : 'Ready for project analysis'}</div>
                <div className="compat-status-msg">{inventory?.recommended_path || 'Upload a project zip or fetch a GitHub repository to generate the migration plan.'}</div>
              </div>
              <div className="compat-right">
                <Metric label="Projects" value={inventory?.project_count || 0} />
                <Metric label="Files" value={inventory?.source_file_count || 0} />
                <Metric label="Score" value={inventory?.complexity?.score || 0} />
              </div>
            </div>
          </div>
        </section>

        <section className="two-col">
          <article className="ma-card">
            <div className="ma-card-header"><div className="ma-card-title">Upload Source</div></div>
            <div className="ma-card-body">
              <div className="upload-tabs">
                <button className={uploadMode === 'local' ? 'active' : ''} onClick={() => setUploadMode('local')}>Local Files</button>
                <button className={uploadMode === 'github' ? 'active' : ''} onClick={() => setUploadMode('github')}>GitHub URL</button>
              </div>
              {uploadMode === 'local' ? (
                <div className="upload-zone" onClick={() => inputRef.current?.click()}>
                  <div className="upload-icon">ZIP</div>
                  <h3>Drop or browse for .zip, .sln, .csproj, .cs files</h3>
                  <p>The backend ignores .git, bin, obj, packages, and node_modules.</p>
                  <input ref={inputRef} hidden type="file" multiple accept=".zip,.sln,.csproj,.cs,.config,.json,.razor,.cshtml" onChange={uploadSelected} />
                  <button className="browse-btn">{busy === 'upload' ? 'Uploading...' : 'Browse Files'}</button>
                </div>
              ) : (
                <div className="github-input-area">
                  <input className="github-input" value={githubUrl} onChange={(event) => setGithubUrl(event.target.value)} placeholder="https://github.com/owner/repo" />
                  <button className="browse-btn" disabled={busy === 'github'} onClick={fetchGithub}>{busy === 'github' ? 'Fetching...' : 'Fetch Repository'}</button>
                </div>
              )}
              <div className="file-list">{files.map((file) => <div className="file-item" key={file.name}><span>{file.type}</span><span className="fn">{file.name}</span><span className="fs">{file.size}</span></div>)}</div>
            </div>
          </article>

          <article className="ma-card">
            <div className="ma-card-header"><div className="ma-card-title">Migration Controls</div></div>
            <div className="ma-card-body">
              <div className="scope-grid">{scopes.map((s) => <div className="scope-card on" key={s.key}><div className="si">{String(s.value()).slice(0, 4)}</div><div className="sl">{s.label}</div></div>)}</div>
              <button className="run-btn" disabled={!files.length || busy === 'migration'} onClick={startMigration}>{busy === 'migration' ? 'Migration running...' : 'Run Migration'}</button>
              <button className="secondary-run" disabled={!files.length} onClick={runAnalyze}>Refresh Analysis</button>
              {runtime && <div className="runtime-box">{runtime.agent_framework.detail}<br />LLM: {runtime.llm.provider} / {runtime.llm.model}</div>}
            </div>
          </article>
        </section>

        <section className="ma-card">
          <div className="ma-card-header"><div className="ma-card-title">Execution Progress</div><span className="section-status">{job?.progress || 'Waiting for migration to start'}</span></div>
          <div className="ma-card-body">
            <div className="stepper">
              <div className="stepper-progress" style={{ width: `${job ? Math.min(100, (stageIndex + 1) * 22) : 0}%` }}></div>
              {steps.map((step, index) => {
                const state = getStepState(index, stageIndex, job);
                return <div className="step-item" key={step}><div className={`step-circle ${state}`}>{state === 'completed' ? 'OK' : index + 1}</div><div className={`step-label ${state}`}>{step}</div><div className="step-sub">{state === 'completed' ? 'Done' : state === 'active' ? 'Running' : 'Waiting'}</div></div>;
              })}
            </div>
            <pre className="terminal">{terminal}</pre>
          </div>
        </section>

        <ReadinessScorecard readiness={report?.readiness} inventory={inventory} />

        <section className="two-col">
          <Findings inventory={inventory} report={report} />
          <Actions job={job} />
        </section>

        <section className="ma-card">
          <div className="ma-card-header"><div className="ma-card-title">Generated Outputs</div><span className="section-status">{report ? 'Ready' : 'Available after migration'}</span></div>
          <div className="ma-card-body outputs-grid">
            <Output title="Migration Summary" ready={!!report} onClick={() => setSelectedOutput(outputContent('Migration Summary', report))} />
            <Output title="Dependency Map" ready={!!report?.dependency_map} onClick={() => setSelectedOutput(outputContent('Dependency Map', report.dependency_map))} />
            <Output title="Validation Report" ready={!!report?.validation} onClick={() => setSelectedOutput(outputContent('Validation Report', report.validation))} />
            <Output title="Migration Diff" ready={!!report?.diff} onClick={() => setSelectedOutput(outputContent('Migration Diff', report.diff))} />
            <Output title="Code Rewrite Preview" ready={!!report?.code_rewrite_previews} onClick={() => setSelectedOutput(outputContent('Code Rewrite Preview', report.code_rewrite_previews))} />
            <Output title="Build Error AI Fixer" ready={!!report?.build_fixer} onClick={() => setSelectedOutput(outputContent('Build Error AI Fixer', report.build_fixer))} />
            <Output title="Dependency Assistant" ready={!!report?.dependency_modernization} onClick={() => setSelectedOutput(outputContent('Dependency Assistant', report.dependency_modernization))} />
            <Output title="Architecture Suggestions" ready={!!report?.architecture_suggestions} onClick={() => setSelectedOutput(outputContent('Architecture Suggestions', report.architecture_suggestions))} />
            <Output title="Test Generation Agent" ready={!!report?.generated_tests} onClick={() => setSelectedOutput(outputContent('Test Generation Agent', report.generated_tests))} />
            <Output title="Executive Report" ready={!!report?.executive_report} onClick={() => setSelectedOutput(outputContent('Executive Report', report.executive_report))} />
            <Output title="Manual Fix List" ready={!!report} onClick={() => setSelectedOutput(outputContent('Manual Fix List', report?.manual_fixes || []))} />
            <Output title="Auth Migration Report" ready={!!report?.auth_migration?.status} onClick={() => setSelectedOutput(outputContent('Auth Migration Report', report.auth_migration))} />
            <Output title="Change Log" ready={!!report} onClick={() => setSelectedOutput(outputContent('Change Log', report?.changes || []))} />
            <Output title="Migrated Project Zip" ready={job?.status === 'completed'} onClick={() => window.location.href = `${API_BASE}/api/files/download`} />
          </div>
          {selectedOutput && <OutputDetail output={selectedOutput} jobId={job?.job_id} />}
        </section>

        <section className="ma-card">
          <div className="ma-card-header">
            <div className="ma-card-title">Run Migrated Application</div>
            <span className="section-status">{appRuntime.status}</span>
          </div>
          <div className="ma-card-body runtime-panel">
            <div className="runtime-actions">
              <button className="run-btn compact-run" disabled={job?.status !== 'completed' || busy === 'runtime'} onClick={startMigratedApp}>{busy === 'runtime' ? 'Starting...' : 'Run Migrated App'}</button>
              <button className="secondary-run inline" disabled={!job?.job_id} onClick={refreshMigratedApp}>Refresh Logs</button>
              <button className="secondary-run inline smoke" disabled={job?.status !== 'completed' || busy === 'smoke'} onClick={runSmokeTest}>{busy === 'smoke' ? 'Testing...' : 'Run Smoke Test'}</button>
              <button className="secondary-run inline danger" disabled={!job?.job_id} onClick={stopMigratedApp}>Stop</button>
            </div>
            <AppStatusMessage runtime={appRuntime} smokeTest={smokeTest} />
            {smokeTest && <SmokeTestResult smokeTest={smokeTest} />}
            <div className="runtime-url">
              <span>Application URL</span>
              {appRuntime.url && appRuntime.status === 'running' ? <a href={appRuntime.url} target="_blank" rel="noreferrer">{appRuntime.url}</a> : <strong>{appRuntime.url ? `${appRuntime.url} (${appRuntime.status})` : 'Available after runtime starts'}</strong>}
            </div>
            <pre className="runtime-logs">{(appRuntime.logs || []).join('\n') || 'Runtime logs will appear here after you start the migrated app.'}</pre>
          </div>
        </section>
      </main>
    </>
  );
}

function Select({ label, value, setValue, values }) {
  return <div className="version-group"><label>{label}</label><select className="version-select" value={value} onChange={(event) => setValue(event.target.value)}>{values.map((item) => <option key={item}>{item}</option>)}</select></div>;
}

function Metric({ label, value }) {
  return <div className="compat-metric"><span className="cm-label">{label}</span><div className="cm-bar"><div className="cm-fill effort" style={{ width: `${Math.min(100, Number(value) || 0)}%` }}></div></div><span className="cm-val">{value}</span></div>;
}

function ReadinessScorecard({ readiness, inventory }) {
  const fallback = inventory ? {
    score: Math.max(0, 100 - Number(inventory?.complexity?.score || 0)),
    level: 'Pre-migration estimate',
    summary: inventory.recommended_path,
    categories: [
      { name: 'Project Compatibility', score: Math.max(0, 100 - Number(inventory?.complexity?.score || 0)), status: 'Estimate', description: 'Inventory-based readiness estimate' },
      { name: 'Legacy Findings', score: Math.max(0, 100 - (inventory?.patterns?.length || 0) * 8), status: 'Estimate', description: `${inventory?.patterns?.length || 0} migration findings detected` },
    ],
    recommendations: ['Run migration to generate the full readiness scorecard.'],
  } : null;
  const data = readiness || fallback;
  if (!data) return null;
  return (
    <section className="ma-card readiness-card">
      <div className="ma-card-header">
        <div className="ma-card-title">Readiness Scorecard</div>
        <span className="section-status">{data.level}</span>
      </div>
      <div className="ma-card-body readiness-body">
        <div className="readiness-score">
          <div className="score-ring" style={{ '--score': `${data.score}%` }}>
            <span>{data.score}</span>
          </div>
          <div>
            <h3>{data.level}</h3>
            <p>{data.summary}</p>
          </div>
        </div>
        <div className="readiness-grid">
          {(data.categories || []).map((item) => (
            <article className={`readiness-item ${item.status?.toLowerCase()}`} key={item.name}>
              <div className="readiness-item-top"><strong>{item.name}</strong><span>{item.score}</span></div>
              <div className="readiness-bar"><div style={{ width: `${item.score}%` }}></div></div>
              <p>{item.description}</p>
            </article>
          ))}
        </div>
        <div className="readiness-recs">{(data.recommendations || []).map((item) => <div key={item}>{item}</div>)}</div>
      </div>
    </section>
  );
}

function Findings({ inventory, report }) {
  const critical = inventory?.patterns?.filter((item) => item.severity === 'High') || [];
  const warnings = inventory?.patterns?.filter((item) => item.severity !== 'High') || [];
  return <article className="ma-card"><div className="ma-card-header"><div className="ma-card-title">Key Findings</div></div><div className="ma-card-body"><div className="findings-summary"><Badge label="Critical" count={critical.length} cls="critical" /><Badge label="Warnings" count={warnings.length} cls="warning" /><Badge label="Manual Fixes" count={report?.manual_fixes?.length || 0} cls="info" /></div><div className="findings-list">{[...critical, ...warnings].map((item, i) => <div className={`f-item ${item.severity === 'High' ? 'critical' : 'warning'}`} key={`${item.title}-${i}`}>{item.title}: {item.action}</div>)}</div></div></article>;
}

function Actions({ job }) {
  const agents = [
    { name: 'Ingestion Agent',      role: 'Extract upload and create isolated workspace', stages: ['queued'] },
    { name: 'Analyzer Agent',       role: 'Scan projects, packages and detect patterns',  stages: ['migrating'] },
    { name: 'LLM Migration Agent',  role: 'Rewrite source files to target .NET version',  stages: ['migrating'] },
    { name: 'Auth Agent',           role: 'Detect, migrate and verify authentication',     stages: ['migrating'] },
    { name: 'Fix Agent',            role: 'Apply deterministic structural fixes',          stages: ['migrating'] },
    { name: 'Build Validator',      role: 'Pre-clean legacy files, build and auto-fix',   stages: ['validate', 'completed', 'needs-review'] },
  ];

  const stage = job?.stage || '';
  const completed = job?.status === 'completed' || job?.status === 'needs_review';

  function getState(agent) {
    if (!job) return 'waiting';
    if (completed) return 'done';
    if (agent.stages.includes(stage)) return 'active';
    const order = ['queued', 'migrating', 'validate', 'completed'];
    const agentIdx = Math.max(...agent.stages.map(s => order.indexOf(s)));
    const currentIdx = order.indexOf(stage);
    if (currentIdx > agentIdx) return 'done';
    return 'waiting';
  }

  const stateStyle = {
    done:    { bg: '#22c55e', color: '#fff', icon: '✓' },
    active:  { bg: '#3b82f6', color: '#fff', icon: '●' },
    waiting: { bg: '#e2e8f0', color: '#94a3b8', icon: '○' },
  };

  return (
    <article className="ma-card">
      <div className="ma-card-header"><div className="ma-card-title">Autonomous Agents</div></div>
      <div className="ma-card-body action-list">
        {agents.map((a) => {
          const s = getState(a);
          const st = stateStyle[s];
          return (
            <div className="a-item" key={a.name}>
              <span className="a-icon" style={{ background: st.bg, color: st.color, borderRadius: '50%', width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, flexShrink: 0 }}>{st.icon}</span>
              <span className="a-text"><strong style={{ color: s === 'active' ? '#3b82f6' : s === 'done' ? '#22c55e' : undefined }}>{a.name}</strong><br />{a.role}</span>
            </div>
          );
        })}
      </div>
    </article>
  );
}

function Badge({ label, count, cls }) {
  return <div className={`f-badge ${cls}`}><div className="fc">{count}</div><div className="fl">{label}</div></div>;
}

function Output({ title, ready, onClick }) {
  return <button className={`out-card ${ready ? '' : 'disabled'}`} onClick={ready ? onClick : undefined}><div className="out-icon">DOC</div><div className="out-title">{title}</div><div className="out-desc">{ready ? 'Open or download' : 'Pending'}</div></button>;
}

function OutputDetail({ output, jobId }) {
  return (
    <section className="output-detail">
      <div className="detail-head">
        <h2>{output.title}</h2>
        <div className="report-actions">
          {jobId && <a href={`${API_BASE}/api/migration/report`} target="_blank" rel="noreferrer">Preview Report</a>}
          {jobId && <a href={`${API_BASE}/api/migration/report`}>Download CSV</a>}
          {jobId && <a href={`${API_BASE}/api/migration/report`}>Download HTML/PDF</a>}
          <button onClick={() => downloadJson(`${output.title.toLowerCase().replaceAll(' ', '-')}.json`, output.data)}>JSON</button>
        </div>
      </div>
      {output.type === 'summary' && <SummaryDetail report={output.data} />}
      {output.type === 'dependency' && <DependencyDetail inventory={output.data} />}
      {output.type === 'depmap' && <DependencyMapDetail depmap={output.data} />}
      {output.type === 'validation' && <ValidationDetail validation={output.data} />}
      {output.type === 'diff' && <DiffDetail diff={output.data} />}
      {output.type === 'rewrite' && <RewritePreviewDetail items={output.data} />}
      {output.type === 'agentReport' && <AgentReportDetail report={output.data} />}
      {output.type === 'list' && <ListDetail items={output.data} />}
      {output.type === 'auth' && <AuthMigrationDetail auth={output.data} />}
      <pre className="detail-json">{JSON.stringify(output.data, null, 2)}</pre>
    </section>
  );
}

function SummaryDetail({ report }) {
  const inv = report?.inventory || {};
  return <div className="detail-grid"><Card label="From" value={report?.from_version} /><Card label="To" value={report?.to_version} /><Card label="Projects" value={inv.project_count || 0} /><Card label="Build" value={report?.validation?.success ? 'Passed' : 'Needs Review'} /></div>;
}

function DependencyDetail({ inventory }) {
  const projects = inventory?.projects || [];
  return (
    <div className="dependency-detail">
      {projects.map((project) => (
        <div className="dep-card" key={project.path}>
          <strong>{project.path}</strong>
          <span>{project.target_framework || 'No framework detected'}</span>
          {(project.packages || []).map((pkg) => <p key={`${project.path}-${pkg.name}`}>{pkg.name} {pkg.version}</p>)}
        </div>
      ))}
    </div>
  );
}

function ValidationDetail({ validation }) {
  return <div className={`validation-banner ${validation?.success ? 'success' : 'failed'}`}>{validation?.success ? 'Build succeeded' : 'Build failed or needs review'} at stage {validation?.stage || 'unknown'}</div>;
}

function DiffDetail({ diff }) {
  const summary = diff?.summary || {};
  return (
    <div className="diff-detail">
      <div className="detail-grid">
        <Card label="Added" value={summary.added || 0} />
        <Card label="Modified" value={summary.modified || 0} />
        <Card label="Removed" value={summary.removed || 0} />
        <Card label="Unchanged" value={summary.unchanged || 0} />
      </div>
      <div className="diff-columns">
        <FileList title="Added Files" files={diff?.added || []} />
        <FileList title="Modified Files" files={diff?.modified || []} />
        <FileList title="Removed Files" files={diff?.removed || []} />
      </div>
      {(diff?.previews || []).map((preview) => <pre className="diff-preview" key={preview.path}>{preview.diff}</pre>)}
    </div>
  );
}

function RewritePreviewDetail({ items }) {
  return <div className="rewrite-preview">{(items || []).map((item) => <article key={item.path}><h3>{item.path}</h3><p>{item.explanation}</p><div className="rewrite-columns"><pre>{item.legacy}</pre><pre>{item.proposed}</pre></div></article>)}</div>;
}

function AgentReportDetail({ report }) {
  const items = Array.isArray(report) ? report : report?.items || Object.entries(report || {}).map(([key, value]) => ({ name: key, value }));
  return <div className="agent-report">{items.map((item, index) => <article key={index}>{Object.entries(item).map(([key, value]) => <p key={key}><strong>{key}</strong><span>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</span></p>)}</article>)}</div>;
}

function FileList({ title, files }) {
  return <article className="diff-list"><strong>{title}</strong>{files.length ? files.slice(0, 12).map((file) => <span key={file}>{file}</span>) : <span>None</span>}</article>;
}

function SmokeTestResult({ smokeTest }) {
  return (
    <div className={`smoke-result ${smokeTest.status}`}>
      <div className="smoke-summary"><strong>{smokeTest.summary}</strong><span>{smokeTest.url}</span></div>
      <div className="smoke-checks">
        {(smokeTest.checks || []).map((check) => (
          <div className={`smoke-check ${check.passed ? 'passed' : 'failed'}`} key={check.name}>
            <strong>{check.passed ? 'PASS' : 'REVIEW'}</strong>
            <span>{check.name}</span>
            <em>{check.status_code || 'n/a'}</em>
          </div>
        ))}
      </div>
    </div>
  );
}

function ListDetail({ items }) {
  const list = Array.isArray(items) ? items : [];
  return <div className="detail-list">{list.length ? list.map((item, index) => <div key={index}>{typeof item === 'string' ? item : JSON.stringify(item)}</div>) : <div>No items available.</div>}</div>;
}

function Card({ label, value }) {
  return <article><span>{label}</span><strong>{String(value ?? '')}</strong></article>;
}

function outputContent(title, data) {
  const typeByTitle = {
    'Migration Summary': 'summary',
    'Dependency Map': 'depmap',
    'Validation Report': 'validation',
    'Migration Diff': 'diff',
    'Code Rewrite Preview': 'rewrite',
    'Build Error AI Fixer': 'agentReport',
    'Dependency Assistant': 'agentReport',
    'Architecture Suggestions': 'agentReport',
    'Test Generation Agent': 'agentReport',
    'Executive Report': 'agentReport',
    'Manual Fix List': 'list',
    'Change Log': 'list',
    'Auth Migration Report': 'auth',
  };
  const reportKindByTitle = {
    'Migration Summary': 'executive',
    'Dependency Map': 'dependencies',
    'Validation Report': 'build-fixer',
    'Migration Diff': 'diff',
    'Code Rewrite Preview': 'rewrite',
    'Build Error AI Fixer': 'build-fixer',
    'Dependency Assistant': 'dependencies',
    'Architecture Suggestions': 'architecture',
    'Test Generation Agent': 'tests',
    'Executive Report': 'executive',
    'Manual Fix List': 'build-fixer',
    'Change Log': 'diff',
  };
  return { title, type: typeByTitle[title] || 'json', reportKind: reportKindByTitle[title] || 'executive', data };
}

function getStageIndex(job) {
  if (!job) return -1;
  if (job.status === 'completed' || job.status === 'needs_review') return 4;
  const map = {
    queued: 0,
    ingest: 0,
    inventory: 1,
    migrating: 2,
    'upgrade-projects': 2,
    'rewrite-code': 2,
    validate: 3,
    package: 4,
    completed: 4,
    'needs-review': 4,
    failed: 4,
  };
  return map[job.stage] ?? 0;
}

function getStepState(index, stageIndex, job) {
  if (!job || stageIndex < 0) return 'pending';
  if (job.status === 'completed' || job.status === 'needs_review') return 'completed';
  if (index < stageIndex) return 'completed';
  if (index === stageIndex) return 'active';
  return 'pending';
}

async function postJson(url, payload) {
  const response = await fetch(`${API_BASE}${url}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || response.statusText);
  return data;
}

async function postForm(url, form) {
  const response = await fetch(`${API_BASE}${url}`, { method: 'POST', body: form });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(`${API_BASE}${url}`, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}

function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

function OllamaStatus({ status }) {
  if (!status) return <span className="hero-pill"><span className="pill-dot" style={{ background: '#888' }}></span>Backend: checking...</span>;
  const connected = status.connected;
  return (
    <span className="hero-pill">
      <span className="pill-dot" style={{ background: connected ? '#22c55e' : '#ef4444' }}></span>
      Backend: {connected ? `${status.status} — ${status.model}` : status.status}
    </span>
  );
}

function AppStatusMessage({ runtime, smokeTest }) {
  if (!runtime || runtime.status === 'stopped') return null;

  const statusConfig = {
    starting:    { color: '#f59e0b', icon: '...', text: 'Starting up',                    reason: 'The migrated app is launching. This may take a few seconds.' },
    running:     { color: '#22c55e', icon: 'OK',  text: 'App is running',                  reason: 'The migrated app started and is listening. Run a smoke test to verify the endpoints.' },
    needs_setup: { color: '#f59e0b', icon: '!',   text: 'Setup required before running',   reason: (runtime.logs || []).filter(l => l.trim()).join(' ') },
    exited:      { color: '#ef4444', icon: 'X',   text: 'App exited unexpectedly',          reason: 'The app started but crashed — usually a missing database or misconfigured connection string. Download the zip, fix the config, and run locally.' },
    failed:      { color: '#ef4444', icon: 'X',   text: 'App failed to start',              reason: runtime.logs?.find(l => l.includes('error') || l.includes('Error') || l.includes('Cannot open')) || 'Could not start. Check that .NET SDK is installed and the project builds cleanly.' },
    stopped:     { color: '#6b7280', icon: '-',   text: 'App stopped',                      reason: 'The application was stopped.' },
  };

  const cfg = statusConfig[runtime.status] || { color: '#6b7280', icon: '?', text: runtime.status, reason: '' };

  const smokeColor = !smokeTest ? null
    : smokeTest.status === 'passed'       ? '#22c55e'
    : smokeTest.status === 'needs_review' ? '#f59e0b'
    : '#ef4444';
  const smokeText = !smokeTest ? null
    : smokeTest.status === 'passed'       ? 'Smoke test passed — all required endpoints responded correctly'
    : smokeTest.status === 'needs_review' ? 'Smoke test needs review — some endpoints returned unexpected responses'
    : 'Smoke test failed — app did not respond';

  return (
    <div style={{ margin: '12px 0', padding: '12px 16px', borderRadius: '8px', borderLeft: `4px solid ${smokeColor || cfg.color}`, background: '#f8fafc' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 700, color: smokeColor || cfg.color }}>
        <span style={{ fontSize: '16px' }}>{cfg.icon}</span>
        <span>{smokeText || cfg.text}</span>
      </div>
      <div style={{ marginTop: '4px', fontSize: '13px', color: '#475569' }}>{cfg.reason}</div>
    </div>
  );
}

function DependencyMapDetail({ depmap }) {
  const entries = Object.entries(depmap || {});
  if (!entries.length) return <div className="detail-list"><div>No dependencies detected in migrated output.</div></div>;
  return (
    <div className="dependency-detail">
      {entries.map(([pkg, version]) => (
        <div className="dep-card" key={pkg}>
          <strong>{pkg}</strong>
          <span>{version}</span>
        </div>
      ))}
    </div>
  );
}

function AuthMigrationDetail({ auth }) {
  if (!auth) return <div className="detail-list"><div>No auth migration data available.</div></div>;
  const statusColor = auth.status === 'passed' ? '#22c55e' : auth.status === 'needs_review' ? '#f59e0b' : '#ef4444';
  return (
    <div style={{ padding: '8px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span style={{ fontWeight: 700, color: statusColor, fontSize: 15 }}>{auth.summary}</span>
      </div>
      <div style={{ marginBottom: 8 }}><strong>Auth Type Detected:</strong> {auth.auth_type || 'none'}</div>
      {auth.roles?.length > 0 && <div style={{ marginBottom: 8 }}><strong>Roles:</strong> {auth.roles.join(', ')}</div>}
      {auth.protected_files?.length > 0 && <div style={{ marginBottom: 8 }}><strong>Protected Controllers:</strong> {auth.protected_files.length}</div>}
      {auth.checks?.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <strong>Verification Checks:</strong>
          {auth.checks.map((c, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, fontSize: 13 }}>
              <span style={{ color: c.passed ? '#22c55e' : '#ef4444', fontWeight: 700 }}>{c.passed ? '✓' : '✗'}</span>
              <span>{c.name}</span>
              {!c.passed && <span style={{ color: '#94a3b8', fontSize: 12 }}>— {c.description}</span>}
            </div>
          ))}
        </div>
      )}
      {auth.warnings?.length > 0 && auth.auth_type !== 'none' && (
        <div style={{ background: '#fffbeb', border: '1px solid #fcd34d', borderRadius: 6, padding: '8px 12px' }}>
          <strong style={{ color: '#92400e' }}>Action Required:</strong>
          {auth.warnings.map((w, i) => <div key={i} style={{ fontSize: 13, color: '#78350f', marginTop: 4 }}>⚠ {w}</div>)}
        </div>
      )}
      {auth.changes?.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <strong>Changes Applied:</strong>
          {auth.changes.map((c, i) => <div key={i} style={{ fontSize: 13, color: '#475569', marginTop: 2 }}>• {c}</div>)}
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);

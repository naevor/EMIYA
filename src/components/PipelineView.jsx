import { useMemo, useState } from 'react';

const LEGACY_CHAIN = ['INPUT', 'L-meta', 'L0/L1', 'validator', 'OUT'];
const INACTIVE_STAGES = new Set(['L-meta', 'validator']);
const AGENT_STAGES = new Set(['ROUTE', 'CACHED', 'DECIDE', 'TOOL', 'VOICE']);
const GENERIC_DETAIL_CAP = 2000;

const DETAIL_ORDER = [
  ['model', 'MODEL'],
  ['mood_seed', 'MOOD SEED'],
  ['metrics', 'METRICS'],
  ['thought', 'THOUGHT'],
  ['system_prompt', 'SYSTEM PROMPT'],
  ['raw_response', 'RAW RESPONSE'],
];

function normalizeStepName(name) {
  if (name === 'L0' || name === 'L1') return 'L0/L1';
  return name;
}

function getStep(run, label) {
  return run?.steps?.find((step) => normalizeStepName(step.name) === label);
}

function stringify(value, maxChars = null) {
  if (value == null || value === '') return '';
  const body = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  if (maxChars != null && body.length > maxChars) {
    return `${body.slice(0, maxChars)}\n...[truncated]`;
  }
  return body;
}

function formatMetrics(metrics) {
  if (!metrics) return '';
  const rows = [
    ['tokens/sec', metrics.tokens_per_second],
    ['prompt tokens', metrics.prompt_eval_count],
    ['eval tokens', metrics.eval_count],
    ['total ns', metrics.total_duration],
  ];
  return rows
    .filter(([, value]) => value != null)
    .map(([label, value]) => `${label}: ${value}`)
    .join('\n');
}

function DetailSection({ title, value, defaultOpen = false, maxChars = null }) {
  const body = title === 'METRICS' ? formatMetrics(value) : stringify(value, maxChars);
  if (!body) return null;

  return (
    <details className="pipeline-detail-section" open={defaultOpen}>
      <summary>{title}</summary>
      <pre>{body}</pre>
    </details>
  );
}

function StepDrawer({ run, step, onClose }) {
  const details = step?.details ?? {};
  const extraKeys = Object.keys(details).filter(
    (key) => !DETAIL_ORDER.some(([known]) => known === key) && details[key] != null,
  );

  return (
    <div className="pipeline-drawer">
      <div className="pipeline-drawer__header">
        <span>{step?.name ?? 'STEP'}</span>
        <button type="button" onClick={onClose}>CLOSE</button>
      </div>

      <div className="pipeline-drawer__summary">
        <span>request {run?.request_id?.slice(0, 8) ?? '--'}</span>
        <span>{step?.latency_ms != null ? `${step.latency_ms}ms` : 'no latency'}</span>
        <span>{step?.status ?? 'idle'}</span>
      </div>

      {DETAIL_ORDER.map(([key, label]) => (
        <DetailSection
          key={key}
          title={label}
          value={details[key]}
          defaultOpen={key === 'metrics' || key === 'thought'}
        />
      ))}

      {extraKeys.map((key) => (
        <DetailSection
          key={key}
          title={key.toUpperCase()}
          value={details[key]}
          maxChars={GENERIC_DETAIL_CAP}
        />
      ))}
    </div>
  );
}

export default function PipelineView({ runs }) {
  const [selected, setSelected] = useState(null);
  const run = runs?.length ? runs[runs.length - 1] : null;
  const chainItems = useMemo(() => {
    const hasAgentStages = run?.steps?.some((step) => AGENT_STAGES.has(step.name));
    if (hasAgentStages) {
      return run.steps.map((step, index) => ({
        key: `${index}-${step.name}`,
        label: step.name,
        step,
        legacy: false,
      }));
    }
    return LEGACY_CHAIN.map((label) => ({
      key: label,
      label,
      step: getStep(run, label),
      legacy: true,
    }));
  }, [run]);
  const selectedStep = selected
    ? chainItems.find((item) => item.key === selected)?.step ?? null
    : null;

  const statusLabel = useMemo(() => {
    if (!run) return 'IDLE';
    if (run.status === 'active') return 'RUNNING';
    return run.status?.toUpperCase?.() ?? 'IDLE';
  }, [run]);

  return (
    <div className="panel pipeline-panel">
      <div className="panel__header">
        <span>PIPELINE</span>
        <span className="pipeline-status">{statusLabel}</span>
      </div>
      <div className="panel__body">
        <div className="pipeline-chain">
          {chainItems.map((item) => {
            const { key, label, step, legacy } = item;
            const state = step
              ? step.status
              : legacy && INACTIVE_STAGES.has(label)
                ? 'inactive'
                : legacy && run?.status === 'active' && label === 'L0/L1'
                  ? 'active'
                  : 'idle';
            return (
              <button
                key={key}
                type="button"
                className={`pipeline-step pipeline-step--${state}${selected === key ? ' pipeline-step--selected' : ''}`}
                onClick={() => setSelected(selected === key ? null : key)}
              >
                <span className="pipeline-step__label">{label}</span>
                <span className="pipeline-step__latency">
                  {step?.latency_ms != null ? `${step.latency_ms}ms` : '--'}
                </span>
              </button>
            );
          })}
        </div>

        {run ? (
          <div className="pipeline-meta">
            <span>{run.request_id?.slice(0, 8)}</span>
            <span>{run.latency_ms != null ? `${run.latency_ms}ms` : 'running'}</span>
          </div>
        ) : (
          <div className="pipeline-empty">NO REQUESTS</div>
        )}

        {selectedStep ? (
          <StepDrawer run={run} step={selectedStep} onClose={() => setSelected(null)} />
        ) : null}
      </div>
    </div>
  );
}

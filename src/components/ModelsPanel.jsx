/**
 * ModelsPanel - L-meta / L0 / L1 / L2 statuses.
 *
 * Props:
 *   models: { 'L-meta': 'active', L0: 'active', L1: 'standby', L2: 'offline' }
 */

const STATUS_LABELS = {
  active:   'ACTIVE',
  awaiting: 'AWAIT',
  standby:  'STANDBY',
  recent:   'RECENT',
  offline:  'OFFLINE',
  inactive: 'INACTIVE',
  error:    'ERROR',
};

const STATUS_DOT = {
  active:   '',
  awaiting: 'awaiting',
  standby:  'standby',
  recent:   'standby',
  offline:  'dim',
  inactive: 'dim',
  error:    'offline',
};

const ROLES = {
  'L-meta': 'ORCH',
  L0:       'BASE',
  L1:       'MID',
  L2:       'DEEP',
};

export default function ModelsPanel({ models }) {
  const rows = ['L-meta', 'L0', 'L1', 'L2'];

  return (
    <div className="panel">
      <div className="panel__header">
        <span>MODELS</span>
      </div>
      <div className="panel__body">
        <div className="models-list">
          {rows.map((id) => {
            const status = models?.[id] ?? 'offline';
            return (
              <div key={id} className="models-row">
                <span className="models-row__id">{id}</span>
                <span className="models-row__name">{ROLES[id]}</span>
                <span className="models-row__status">
                  <span className={`status-dot ${STATUS_DOT[status] ?? 'dim'}`} />
                  <span>{STATUS_LABELS[status] ?? '—'}</span>
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

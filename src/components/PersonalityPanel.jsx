import { useState } from 'react';

const TRAITS = [
  { key: 'curiosity', label: 'CURIOSITY' },
  { key: 'bluntness', label: 'BLUNTNESS' },
  { key: 'warmth',    label: 'WARMTH'    },
  { key: 'sarcasm',   label: 'SARCASM'   },
  { key: 'formality', label: 'FORMALITY' },
];

const DEFAULT_PRESETS = ['default', 'unhinged', 'professional', 'tired friend'];

function describeTrait(key, value) {
  if (key === 'curiosity') {
    if (value > 66) return 'notices details';
    if (value < 34) return 'does not pull';
    return 'asks rarely';
  }
  if (key === 'bluntness') {
    if (value > 66) return 'cuts direct';
    if (value < 34) return 'soft edge';
    return 'plain speech';
  }
  if (key === 'warmth') {
    if (value > 66) return 'warmer';
    if (value < 34) return 'cold distance';
    return 'brief warmth';
  }
  if (key === 'sarcasm') {
    if (value > 66) return 'more bite';
    if (value < 34) return 'dry only';
    return 'light irony';
  }
  if (value > 66) return 'formal';
  if (value < 34) return 'loose';
  return 'controlled';
}

const sliderTrack = (value) =>
  `linear-gradient(to right, var(--mint) ${value}%, var(--border) ${value}%)`;

export default function PersonalityPanel({ traits, presets, onChange, onPreset }) {
  const [activePreset, setActivePreset] = useState(null);
  const current = traits ?? {};
  const presetList = Array.isArray(presets) && presets.length ? presets : DEFAULT_PRESETS;

  const updateTrait = (key, value) => {
    setActivePreset(null);
    onChange?.({ ...current, [key]: Number(value) });
  };

  const handlePreset = (preset) => {
    setActivePreset(preset);
    onPreset?.(preset);
  };

  return (
    <div className="panel">
      <div className="panel__header">
        <span>PERSONALITY</span>
      </div>
      <div className="panel__body">
        <div className="trait-presets">
          {presetList.map((preset) => (
            <button
              key={preset}
              type="button"
              className={`trait-preset${activePreset === preset ? ' trait-preset--active' : ''}`}
              onClick={() => handlePreset(preset)}
            >
              {preset}
            </button>
          ))}
        </div>

        <div className="divider" />

        <div className="trait-list">
          {TRAITS.map(({ key, label }) => {
            const value = current[key] ?? 0;
            return (
              <label key={key} className="trait-row">
                <span className="trait-row__top">
                  <span className="trait-row__label">{label}</span>
                  <span className="trait-row__value">{value}</span>
                </span>
                <input
                  className="trait-row__slider"
                  type="range"
                  min="0"
                  max="100"
                  value={value}
                  style={{ background: sliderTrack(value) }}
                  onChange={(e) => updateTrait(key, e.target.value)}
                />
                <span className="trait-row__hint">{describeTrait(key, value)}</span>
              </label>
            );
          })}
        </div>
      </div>
    </div>
  );
}

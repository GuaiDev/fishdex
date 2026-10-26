// Line icons in the mockup's tab-bar style: 24px grid, 1.8 stroke, round caps.
const base = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
};

export const ChatIcon = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" {...base}>
    <path d="M21 12a8 8 0 0 1-11.6 7.2L4 20l1-3.2A8 8 0 1 1 21 12Z" />
  </svg>
);

export const MapIcon = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" {...base}>
    <path d="M12 21s-7-5.5-7-11a7 7 0 0 1 14 0c0 5.5-7 11-7 11Z" />
    <circle cx="12" cy="10" r="2.5" />
  </svg>
);

export const AddIcon = ({ size = 30 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" {...base}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 8v8M8 12h8" />
  </svg>
);

export const LogIcon = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" {...base}>
    <path d="M4 6h16M4 12h16M4 18h10" />
  </svg>
);

export const FishIcon = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" {...base}>
    <path d="M3 12c3-5 9-5 12 0-3 5-9 5-12 0Z" />
    <circle cx="7.5" cy="12" r="1" fill="currentColor" stroke="none" />
    <path d="M15 12l6-3v6l-6-3Z" />
  </svg>
);

export const PlusIcon = ({ size = 18 }) => (
  <svg width={size} height={size} viewBox="0 0 18 18" {...base} strokeWidth={2}>
    <path d="M9 3v12M3 9h12" />
  </svg>
);

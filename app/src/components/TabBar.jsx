import { AddIcon, ChatIcon, FishIcon, LogIcon, MapIcon } from './icons.jsx';
import './tabbar.css';

export const TABS = [
  { id: 'chat', label: 'Chat', Icon: ChatIcon },
  { id: 'map', label: 'Map', Icon: MapIcon },
  { id: 'add', label: 'Add catch', Icon: AddIcon, iconOnly: true },
  { id: 'log', label: 'Log', Icon: LogIcon },
  { id: 'fishdex', label: 'FishDex', Icon: FishIcon },
];

// Glass on the chrome only; the content behind it stays solid.
export default function TabBar({ current, onSelect }) {
  return (
    <nav className="tabbar" aria-label="Main">
      <ul>
        {TABS.map(({ id, label, Icon, iconOnly }) => (
          <li key={id}>
            <button
              type="button"
              className="tab"
              aria-current={current === id ? 'page' : undefined}
              aria-label={iconOnly ? label : undefined}
              onClick={() => onSelect(id)}
            >
              <Icon />
              {!iconOnly && <span>{label}</span>}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

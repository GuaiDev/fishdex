import { useCallback, useEffect, useState } from 'react';
import { hasToken } from './api.js';
import TabBar, { TABS } from './components/TabBar.jsx';
import FishDex from './screens/FishDex.jsx';
import Placeholder from './screens/Placeholder.jsx';
import SignIn from './screens/SignIn.jsx';
import './shell.css';

const DEFAULT_TAB = 'fishdex';
const tabIds = new Set(TABS.map((t) => t.id));

function tabFromHash() {
  const id = window.location.hash.replace(/^#\/?/, '');
  return tabIds.has(id) ? id : DEFAULT_TAB;
}

// Only the FishDex screen is built; the other tabs name the screen they will
// become so the shell can be navigated end to end.
const PLACEHOLDERS = {
  chat: 'Chat',
  map: 'Map',
  add: 'Add catch',
  log: 'Log',
};

export default function App() {
  const [signedIn, setSignedIn] = useState(hasToken);
  const [tab, setTab] = useState(tabFromHash);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const select = useCallback((id) => {
    window.location.hash = `/${id}`;
    window.scrollTo(0, 0);
  }, []);

  if (!signedIn) return <SignIn onSignedIn={() => setSignedIn(true)} />;

  return (
    <>
      <main className="shell-main">
        {tab === 'fishdex' ? (
          <FishDex onAddCatch={() => select('add')} onSignedOut={() => setSignedIn(false)} />
        ) : (
          <Placeholder name={PLACEHOLDERS[tab]} />
        )}
      </main>
      <TabBar current={tab} onSelect={select} />
    </>
  );
}

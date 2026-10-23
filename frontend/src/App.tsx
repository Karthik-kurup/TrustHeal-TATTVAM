/**
 * App.tsx — Application root with routing, WS hook, and persistent layout.
 */

import React, { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import TopBar from './components/TopBar';
import GuidedDemo from './components/GuidedDemo';
import { useWs } from './hooks/useWs';

// ── Error Boundary ─────────────────────────────────────────────────────────────

interface EBState { error: Error | null }
type Theme = 'dark' | 'light';

function loadTheme(): Theme {
  try {
    return window.localStorage.getItem('trustheal-theme') === 'light' ? 'light' : 'dark';
  } catch (error) {
    console.warn('Unable to read saved theme preference:', error);
    return 'dark';
  }
}

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, EBState> {
  state: EBState = { error: null };

  static getDerivedStateFromError(error: Error): EBState {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center min-h-screen bg-[#0f1117] text-gray-100 p-8">
          <div className="max-w-2xl w-full bg-gray-900 border border-red-800 rounded-lg p-6 space-y-4">
            <h1 className="text-lg font-bold text-red-400">Application Error</h1>
            <p className="text-sm text-gray-400">
              A render error occurred. Check the browser console for details.
            </p>
            <pre className="text-xs text-red-300 bg-gray-800 rounded p-3 overflow-auto max-h-64 whitespace-pre-wrap">
              {this.state.error.message}
              {'\n\n'}
              {this.state.error.stack}
            </pre>
            <button
              onClick={() => { this.setState({ error: null }); window.location.reload(); }}
              className="px-4 py-2 rounded bg-gray-700 hover:bg-gray-600 text-sm text-gray-200"
            >
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// Pages
import MissionControl from './pages/MissionControl';
import MetricsExplorer from './pages/MetricsExplorer';
import IncidentWarRoom from './pages/IncidentWarRoom';
import TwinStudio from './pages/TwinStudio';
import ControlCenter from './pages/ControlCenter';
import ModelsAndTrust from './pages/ModelsAndTrust';
import CompareLab from './pages/CompareLab';

/** Root layout component — mounts the WS hook once. */
function Layout() {
  // Connect WebSocket and dispatch events to Zustand stores
  useWs();
  const [demoOpen, setDemoOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>(loadTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem('trustheal-theme', theme);
    } catch (error) {
      console.warn('Unable to save theme preference:', error);
    }
  }, [theme]);

  return (
    <div className="app-shell flex flex-col min-h-screen">
      <TopBar
        onRunDemo={() => setDemoOpen(true)}
        theme={theme}
        onToggleTheme={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')}
      />
      <main className="flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<MissionControl />} />
          <Route path="/metrics" element={<MetricsExplorer />} />
          <Route path="/incidents" element={<IncidentWarRoom />} />
          <Route path="/incidents/:id" element={<IncidentWarRoom />} />
          <Route path="/twin" element={<TwinStudio />} />
          <Route path="/control" element={<ControlCenter />} />
          <Route path="/models" element={<ModelsAndTrust />} />
          <Route path="/compare" element={<CompareLab />} />
        </Routes>
      </main>
      {demoOpen && <GuidedDemo onClose={() => setDemoOpen(false)} />}
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Layout />
      </BrowserRouter>
    </ErrorBoundary>
  );
}

import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link, Navigate } from 'react-router-dom';
import CardListPage from './pages/CardListPage';
import AddCardPage from './pages/AddCardPage';
import CardDetailPage from './pages/CardDetailPage';
import IdentifyPage from './pages/IdentifyPage';
import SealedListPage from './pages/SealedListPage';
import AddSealedPage from './pages/AddSealedPage';
import DashboardPage from './pages/DashboardPage';
import PriceSnapshotPage from './pages/PriceSnapshotPage';
import SettingsPage from './pages/SettingsPage';
import StatusPage from './pages/StatusPage';
import './App.css';

/**
 * Redirect helper for the legacy /cards/edit/:id and /sealed/edit/:id routes.
 * Phase D moved edit links under the new RESTful shape (/cards/:id/edit) but
 * existing bookmarks should keep working forever.
 */
const RedirectEdit = ({ basePath }) => {
  const id = window.location.pathname.split('/').pop();
  return <Navigate replace to={`${basePath}/${id}/edit`} />;
};

function App() {
  return (
    <div className="anime-app">
    <Router>
      <div className="App">
        <header className="App-header">
          <h1>Card Collection Manager</h1>
          <nav>
            <ul>
              <li><Link to="/">Dashboard</Link></li>
              <li><Link to="/cards">My Cards</Link></li>
              <li><Link to="/sealed">Sealed Products</Link></li>
              <li><Link to="/identify">Identify</Link></li>
              <li><Link to="/snapshot">Price Snapshot</Link></li>
              <li><Link to="/settings">Backup</Link></li>
              <li><Link to="/status">Status</Link></li>
            </ul>
          </nav>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/cards" element={<CardListPage />} />
            <Route path="/cards/add" element={<AddCardPage />} />
            {/* New REST shape — preferred. Read-only detail + edit form. */}
            <Route path="/cards/:id" element={<CardDetailPage />} />
            <Route path="/cards/:id/edit" element={<AddCardPage />} />
            {/* Legacy alias kept for any old bookmarks. */}
            <Route path="/cards/edit/:id" element={<RedirectEdit basePath="/cards" />} />
            {/* DeepSeek multimodal: drop photos → ranked candidates → existing resolver. */}
            <Route path="/identify" element={<IdentifyPage />} />
            <Route path="/sealed" element={<SealedListPage />} />
            <Route path="/sealed/add" element={<AddSealedPage />} />
            <Route path="/sealed/:id/edit" element={<AddSealedPage />} />
            <Route path="/sealed/edit/:id" element={<RedirectEdit basePath="/sealed" />} />
            <Route path="/snapshot" element={<PriceSnapshotPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/status" element={<StatusPage />} />
          </Routes>
        </main>
      </div>
    </Router>
    </div>
  );
}

export default App;

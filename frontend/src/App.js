import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom';
import CardListPage from './pages/CardListPage';
import AddCardPage from './pages/AddCardPage';
import SealedListPage from './pages/SealedListPage';
import AddSealedPage from './pages/AddSealedPage';
import DashboardPage from './pages/DashboardPage';
import PriceSnapshotPage from './pages/PriceSnapshotPage';
import './App.css';

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
              <li><Link to="/snapshot">Price Snapshot</Link></li>
            </ul>
          </nav>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/cards" element={<CardListPage />} />
            <Route path="/cards/add" element={<AddCardPage />} />
            <Route path="/cards/edit/:id" element={<AddCardPage />} />
            <Route path="/sealed" element={<SealedListPage />} />
            <Route path="/sealed/add" element={<AddSealedPage />} />
            <Route path="/sealed/edit/:id" element={<AddSealedPage />} />
            <Route path="/snapshot" element={<PriceSnapshotPage />} />
          </Routes>
        </main>
      </div>
    </Router>
    </div>
  );
}

export default App;

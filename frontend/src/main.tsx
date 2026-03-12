import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import BacktestPage from './pages/BacktestPage.tsx'
import { Navbar } from './components/Navbar.tsx'

function Root() {
  const [page, setPage] = useState<"dashboard" | "backtest">("dashboard");

  return (
    <>
      <Navbar page={page} setPage={setPage} />
      <div style={{ display: page === "dashboard" ? "block" : "none" }}>
        <App />
      </div>
      <div style={{ display: page === "backtest" ? "block" : "none" }}>
        <BacktestPage />
      </div>
    </>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)

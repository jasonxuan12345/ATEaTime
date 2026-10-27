import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ReleaseCheckPage } from './features/release-check/ReleaseCheckPage';
import './styles/tokens.css';
import './styles/workbench-tokens.css';
import './styles/base.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode><BrowserRouter><ReleaseCheckPage /></BrowserRouter></StrictMode>,
);

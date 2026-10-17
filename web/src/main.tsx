import { StrictMode } from 'react'
import { createRoot, hydrateRoot } from 'react-dom/client'
import './index.css'
import App from './App'

const root = document.getElementById('root')!
const app = (
  <StrictMode>
    <App />
  </StrictMode>
)

// In production every page arrives prerendered: React only takes over the HTML that
// is already on screen. In development #root arrives empty and is built from scratch.
if (root.firstElementChild) hydrateRoot(root, app)
else createRoot(root).render(app)

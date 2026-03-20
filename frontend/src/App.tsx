import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './components/Layout'
import { LibraryPage } from './pages/LibraryPage'
import { BenchmarkPage } from './pages/BenchmarkPage'
import { ResultsPage } from './pages/ResultsPage'
import { ModelsPage } from './pages/ModelsPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/library" replace />} />
          <Route path="library" element={<LibraryPage />} />
          <Route path="benchmark" element={<BenchmarkPage />} />
          <Route path="results" element={<ResultsPage />} />
          <Route path="models" element={<ModelsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

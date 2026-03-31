import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './components/Layout'
import { LibraryPage } from './pages/LibraryPage'
import { BenchmarkPage } from './pages/BenchmarkPage'
import { ResultsPage } from './pages/ResultsPage'
import { ModelsPage } from './pages/ModelsPage'
import { TuningPage } from './pages/TuningPage'
import { HWFlowPage } from './pages/HWFlowPage'
import { TranscribePage } from './pages/TranscribePage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/library" replace />} />
          <Route path="library" element={<LibraryPage />} />
          <Route path="benchmark" element={<BenchmarkPage />} />
          <Route path="results" element={<ResultsPage />} />
          <Route path="tuning" element={<TuningPage />} />
          <Route path="models" element={<ModelsPage />} />
          <Route path="prepis" element={<TranscribePage />} />
          <Route path="hwflow" element={<HWFlowPage />} />
          <Route path="hw-flow" element={<HWFlowPage />} />
          <Route path="HWFlow" element={<HWFlowPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

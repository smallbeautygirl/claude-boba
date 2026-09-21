import { BrowserRouter, Route, Routes } from "react-router-dom";
import { JobDetail } from "./pages/JobDetail";
import { Submit } from "./pages/Submit";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Submit />} />
        <Route path="/jobs/:id" element={<JobDetail />} />
      </Routes>
    </BrowserRouter>
  );
}

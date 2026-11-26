import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { useRobotStream } from "./hooks/useRobotStream";
import { Dashboard } from "./pages/Dashboard";
import { DiagnosticsPage } from "./pages/Diagnostics";
import { EventTimeline } from "./pages/EventTimeline";
import { ExperimentLab } from "./pages/ExperimentLab";
import { JointControl } from "./pages/JointControl";
import { SettingsPage } from "./pages/Settings";
import { TaskTakeover } from "./pages/TaskTakeover";
import { TrajectoryInspector } from "./pages/TrajectoryInspector";
import { VisualWorkbench } from "./pages/VisualWorkbench";

export default function App() {
  useRobotStream();
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route path="joints" element={<JointControl />} />
        <Route path="task" element={<TaskTakeover />} />
        <Route path="diagnostics" element={<DiagnosticsPage />} />
        <Route path="trajectory" element={<TrajectoryInspector />} />
        <Route path="experiments" element={<ExperimentLab />} />
        <Route path="visual" element={<VisualWorkbench />} />
        <Route path="events" element={<EventTimeline />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

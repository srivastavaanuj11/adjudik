import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { landingFor } from "./nav";
import { Layout } from "./Layout";
import { RequireAuth, RequireRole } from "./guards";
import { Login } from "../screens/Login";
import { Password } from "../screens/Password";
import { Reviews } from "../screens/Reviews";
import { ReviewWorkspace } from "../screens/review/ReviewWorkspace";
import { Evaluations } from "../screens/Evaluations";
import { EvaluationDetail } from "../screens/EvaluationDetail";
import { Decisions } from "../screens/Decisions";
import { DecisionDetail } from "../screens/DecisionDetail";
import { Audit } from "../screens/Audit";
import { Settings } from "../screens/Settings";

function LandingRedirect() {
  const { role } = useAuth();
  return <Navigate to={landingFor(role)} replace />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/password" element={<Password />} />

      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<LandingRedirect />} />

        <Route
          path="reviews"
          element={
            <RequireRole roles={["reviewer", "admin", "auditor"]}>
              <Reviews />
            </RequireRole>
          }
        />
        <Route
          path="reviews/:id"
          element={
            <RequireRole roles={["reviewer", "admin"]}>
              <ReviewWorkspace />
            </RequireRole>
          }
        />

        <Route
          path="evaluations"
          element={
            <RequireRole roles={["admin", "author", "auditor"]}>
              <Evaluations />
            </RequireRole>
          }
        />
        <Route
          path="evaluations/:id"
          element={
            <RequireRole roles={["admin", "author", "auditor"]}>
              <EvaluationDetail />
            </RequireRole>
          }
        />

        <Route
          path="decisions"
          element={
            <RequireRole roles={["admin", "author", "auditor"]}>
              <Decisions />
            </RequireRole>
          }
        />
        <Route
          path="decisions/:key/*"
          element={
            <RequireRole roles={["admin", "author", "auditor"]}>
              <DecisionDetail />
            </RequireRole>
          }
        />

        <Route
          path="audit"
          element={
            <RequireRole roles={["auditor", "admin"]}>
              <Audit />
            </RequireRole>
          }
        />

        <Route
          path="settings/*"
          element={
            <RequireRole roles={["admin"]}>
              <Settings />
            </RequireRole>
          }
        />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

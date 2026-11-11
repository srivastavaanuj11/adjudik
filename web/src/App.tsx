import { AuthProvider } from "./auth/AuthContext";
import { AppRoutes } from "./app/routes";

// App assumes it is rendered inside a Router and a QueryClientProvider
// (wired in main.tsx, or by the test harness).
export function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}

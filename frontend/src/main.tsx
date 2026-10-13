import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";

import { ApiError, setUnauthorizedHandler } from "./api/client";
import { keys } from "./api/hooks";
import type { Session } from "./api/types";
import { Layout, RequireAuth } from "./components/Layout";
import { ToastProvider } from "./components/Toasts";
import "./index.css";
import { AlertsPage } from "./pages/AlertsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DeviceDetailPage } from "./pages/DeviceDetailPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { TokensPage } from "./pages/TokensPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Don't hammer the API on auth/permission/not-found errors.
      retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
      refetchOnWindowFocus: true,
    },
  },
});

// Session expired or revoked: flip to signed-out so <RequireAuth> redirects.
setUnauthorizedHandler(() => {
  queryClient.setQueryData<Session>(keys.session, { authenticated: false, user: null });
});

const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    element: (
      <RequireAuth>
        <Layout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "devices/:id", element: <DeviceDetailPage /> },
      { path: "alerts", element: <AlertsPage /> },
      { path: "tokens", element: <TokensPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      {/* "user": honour prefers-reduced-motion (transforms off, opacity kept). */}
      <MotionConfig reducedMotion="user">
        <ToastProvider>
          <RouterProvider router={router} />
        </ToastProvider>
      </MotionConfig>
    </QueryClientProvider>
  </StrictMode>,
);

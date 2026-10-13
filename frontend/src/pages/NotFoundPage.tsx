import { Link } from "react-router";

import { EmptyState } from "../components/ui";

export function NotFoundPage() {
  return (
    <EmptyState title="Page not found">
      <Link to="/" className="text-accent hover:underline">
        Back to the dashboard
      </Link>
    </EmptyState>
  );
}

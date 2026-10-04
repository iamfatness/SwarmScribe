import { Link } from "../app/router";
import { usePageTitle } from "../app/usePageTitle";

export function NotFoundPage() {
  usePageTitle("Not found");
  return (
    <>
      <h1>Page not found</h1>
      <p>
        There is no console page at this address. <Link to="/">Go to the fleet overview</Link>.
      </p>
    </>
  );
}

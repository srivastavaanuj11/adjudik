import { useQuery } from "@tanstack/react-query";

type Readyz = { status: string; checks?: Record<string, string> };

async function fetchReadyz(): Promise<Readyz> {
  const resp = await fetch("/readyz");
  return (await resp.json()) as Readyz;
}

export function App() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["readyz"],
    queryFn: fetchReadyz,
    retry: false,
  });

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 640, margin: "4rem auto", padding: "0 1rem" }}>
      <h1>Adjudik</h1>
      <p>
        Decision engine dashboard. This is the M0 scaffold — real screens (reviews,
        evaluations, decisions, policies, metrics, audit) arrive in M4.
      </p>
      <section aria-label="server status">
        <h2>Server status</h2>
        {isLoading && <p>Checking…</p>}
        {isError && <p>Server unreachable.</p>}
        {data && (
          <ul>
            <li>status: {data.status}</li>
            {data.checks &&
              Object.entries(data.checks).map(([k, v]) => (
                <li key={k}>
                  {k}: {v}
                </li>
              ))}
          </ul>
        )}
      </section>
    </main>
  );
}

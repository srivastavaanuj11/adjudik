import { useQuery } from "@tanstack/react-query";
import { decisions } from "../../api/endpoints";

/** Fetch one version's full detail, enabled only when a version number is known. */
export function useVersion(key: string, n: number | null | undefined) {
  return useQuery({
    queryKey: ["decision", key, "version", n],
    queryFn: () => decisions.version(key, n as number),
    enabled: n !== null && n !== undefined,
  });
}

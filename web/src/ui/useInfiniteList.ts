import { useInfiniteQuery } from "@tanstack/react-query";
import type { Paginated } from "../api/types";

/**
 * Wraps a cursor-paginated endpoint (§11.1) as an infinite query and flattens
 * the accumulated rows. `fetchPage(cursor)` returns one `Paginated<T>`.
 */
export function useInfiniteList<T>(
  key: unknown[],
  fetchPage: (cursor?: string) => Promise<Paginated<T>>,
  enabled = true,
) {
  const query = useInfiniteQuery({
    queryKey: key,
    enabled,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => fetchPage(pageParam),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const rows: T[] = query.data?.pages.flatMap((p) => p.results) ?? [];
  return { query, rows };
}

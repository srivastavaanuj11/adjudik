import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "../api/client";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (count, error) => {
          // Don't retry auth/permission/validation errors; retry transient ones twice.
          if (error instanceof ApiError && error.status < 500 && error.status !== 429) {
            return false;
          }
          return count < 2;
        },
        staleTime: 10_000,
        refetchOnWindowFocus: false,
      },
    },
  });
}

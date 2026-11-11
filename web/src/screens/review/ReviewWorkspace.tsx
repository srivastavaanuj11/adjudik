import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { reviews } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import { Loading, ErrorState } from "../../ui/states";
import type { ReviewCompleteBody, ReviewDetail } from "../../api/types";
import { ReviewBody } from "./ReviewBody";

const HEARTBEAT_MS = 30_000;

export function ReviewWorkspace() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const navigate = useNavigate();

  const [autoAdvance, setAutoAdvance] = useState(true);
  const [banner, setBanner] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const claimedRef = useRef(false);

  const query = useQuery({
    queryKey: ["review", id],
    queryFn: () => reviews.get(id),
    staleTime: 60_000,
  });

  const claim = useMutation({
    mutationFn: () => reviews.claim(id),
    onSuccess: (task) => qc.setQueryData(["review", id], task),
    onError: (e) => {
      if (e instanceof ApiError && e.code === "lease_lost") {
        setBanner("This task is claimed by someone else.");
      } else if (e instanceof ApiError && e.status === 403) {
        setBanner("This workspace uses next-only mode — use “Start reviewing” from the queue.");
      } else {
        setBanner(e instanceof ApiError ? e.message : "Could not claim the task.");
      }
    },
  });

  // Claim once if the loaded task is still open.
  useEffect(() => {
    if (query.data && query.data.status === "open" && !claimedRef.current) {
      claimedRef.current = true;
      claim.mutate();
    }
  }, [query.data, claim]);

  // Heartbeat to keep the lease while reviewing.
  const task = query.data;
  const claimedByMe = task?.status === "claimed";
  useEffect(() => {
    if (!claimedByMe) return;
    const h = setInterval(() => {
      reviews.heartbeat(id).catch((e) => {
        if (e instanceof ApiError && e.code === "lease_lost") {
          setBanner("Your lease expired — returning to the queue.");
          setTimeout(() => navigate("/reviews"), 1500);
        }
      });
    }, HEARTBEAT_MS);
    return () => clearInterval(h);
  }, [claimedByMe, id, navigate]);

  const complete = useMutation({
    mutationFn: (body: ReviewCompleteBody) => reviews.complete(id, body),
    onSuccess: async () => {
      setServerError(null);
      await qc.invalidateQueries({ queryKey: ["reviews"] });
      if (autoAdvance) {
        try {
          const next = await reviews.next({});
          if (next) {
            qc.setQueryData(["review", next.id], next);
            navigate(`/reviews/${next.id}`);
            return;
          }
        } catch {
          /* fall through to the queue */
        }
      }
      navigate("/reviews");
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "lease_lost") {
        setBanner("Your lease was lost. Returning to the queue.");
        setTimeout(() => navigate("/reviews"), 1500);
      } else {
        setServerError(e instanceof ApiError ? e.message : "Could not submit the review.");
      }
    },
  });

  const release = useMutation({
    mutationFn: () => reviews.release(id),
    onSuccess: () => navigate("/reviews"),
    onError: () => navigate("/reviews"),
  });

  if (query.isPending) return <Loading label="Opening review…" />;
  if (query.isError) {
    return (
      <div>
        <p>
          <Link to="/reviews">← Queue</Link>
        </p>
        <ErrorState error={query.error} />
      </div>
    );
  }

  const busy = claim.isPending || complete.isPending || release.isPending;

  return (
    <div>
      <p>
        <Link to="/reviews">← Queue</Link>
      </p>
      {banner && (
        <div className="banner danger" role="alert">
          {banner} <Link to="/reviews">Back to queue</Link>
        </div>
      )}
      <ReviewBody
        task={task as ReviewDetail}
        busy={busy}
        serverError={serverError}
        autoAdvance={autoAdvance}
        onAutoAdvanceChange={setAutoAdvance}
        onComplete={(body) => complete.mutate(body)}
        onRelease={() => release.mutate()}
        leaseExpiresAt={task?.lease_expires_at ?? null}
      />
    </div>
  );
}

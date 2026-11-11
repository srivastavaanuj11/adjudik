import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { ReviewBody } from "./ReviewBody";
import type { ReviewDetail } from "../../api/types";

function makeTask(overrides: Partial<ReviewDetail> = {}): ReviewDetail {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    kind: "gate",
    reason: "automation_disabled",
    status: "claimed",
    blind: false,
    lease_expires_at: null,
    due_at: new Date(Date.now() + 600_000).toISOString(),
    decision: {
      key: "refund_approval",
      name: "Refund approval",
      version: 1,
      outcomes: [
        { id: "approve", label: "Approve" },
        { id: "deny", label: "Deny" },
      ],
      reason_codes: [],
    },
    case: { input: { order: { amount: 120 } }, included_paths: ["/order/amount"], external_id: "A-12" },
    rules: { inputs: [], rows: [], trace: null },
    suggestion: null,
    prior_evaluations: [],
    ...overrides,
  };
}

const noop = () => {};

function renderBody(task: ReviewDetail) {
  return render(
    <ReviewBody
      task={task}
      busy={false}
      autoAdvance
      onAutoAdvanceChange={noop}
      onComplete={noop}
      onRelease={noop}
      leaseExpiresAt={null}
    />,
  );
}

test("a blind task never renders a suggestion", () => {
  renderBody(makeTask({ blind: true, suggestion: null }));
  expect(screen.queryByLabelText("model suggestion")).toBeNull();
  expect(screen.queryByRole("heading", { name: "Suggestion" })).toBeNull();
  expect(screen.queryByRole("button", { name: /accept suggestion/i })).toBeNull();
  // The reviewer still gets the full outcome list to decide from.
  expect(screen.getByRole("radio", { name: /approve/i })).toBeInTheDocument();
});

test("a visible suggestion shows the outcome and rationale but never a confidence", () => {
  renderBody(
    makeTask({
      blind: false,
      suggestion: { outcome: "approve", rationale: "The item arrived damaged." },
    }),
  );
  const card = screen.getByLabelText("model suggestion");
  expect(card).toBeInTheDocument();
  expect(card).toHaveTextContent("approve");
  expect(card).toHaveTextContent("The item arrived damaged.");
  expect(card).toHaveTextContent(/Model-generated/i);
  // No confidence or threshold is ever rendered in the review workspace (§12.2).
  expect(card.textContent ?? "").not.toMatch(/confidence|threshold/i);
  expect(card.textContent ?? "").not.toMatch(/\d\.\d/); // no decimal score
  expect(screen.getByRole("button", { name: /accept suggestion/i })).toBeInTheDocument();
});

test("keyboard: digit selects, override needs a reason, Ctrl+Enter submits an accept", () => {
  const onComplete = vi.fn();
  render(
    <ReviewBody
      task={makeTask({
        suggestion: { outcome: "approve", rationale: "ok" },
      })}
      busy={false}
      autoAdvance
      onAutoAdvanceChange={noop}
      onComplete={onComplete}
      onRelease={noop}
      leaseExpiresAt={null}
    />,
  );

  // "2" selects Deny; that differs from the suggestion, so submit is an override
  // and is blocked until a reason is given.
  fireEvent.keyDown(document, { key: "2" });
  fireEvent.keyDown(document, { key: "Enter", ctrlKey: true });
  expect(onComplete).not.toHaveBeenCalled();
  expect(screen.getByRole("alert")).toHaveTextContent(/reason is required/i);

  // "1" selects Approve (== suggestion) → accept; Ctrl+Enter submits.
  fireEvent.keyDown(document, { key: "1" });
  fireEvent.keyDown(document, { key: "Enter", ctrlKey: true });
  expect(onComplete).toHaveBeenCalledWith(
    expect.objectContaining({ action: "accept", outcome: "approve" }),
  );
});

test("Esc releases when no text field is focused", () => {
  const onRelease = vi.fn();
  render(
    <ReviewBody
      task={makeTask()}
      busy={false}
      autoAdvance
      onAutoAdvanceChange={noop}
      onComplete={noop}
      onRelease={onRelease}
      leaseExpiresAt={null}
    />,
  );
  fireEvent.keyDown(document, { key: "Escape" });
  expect(onRelease).toHaveBeenCalled();
});

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CreditsBanner, CreditsWarning, RunCreditsNotice } from "./components/credits";
import { StatusBadge } from "./components/ui";
import { LlmStatusProvider } from "./llmStatus";

function mockHealth(llm: object) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ status: "ok", api_key_configured: true, model: "m", mcp_tools: 20, mcp_transport: "stdio", llm }), { status: 200 })),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("credits messaging", () => {
  it("shows a site-wide banner and a run warning when the server is out of credits", async () => {
    mockHealth({ status: "credits_exhausted", message: "out", since: 1 });
    render(
      <LlmStatusProvider>
        <CreditsBanner />
        <CreditsWarning />
      </LlmStatusProvider>,
    );
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Out of API credits"));
    expect(screen.getByRole("status")).toHaveTextContent("out of API credits");
  });

  it("shows nothing when the API is fine", async () => {
    mockHealth({ status: "ok" });
    render(
      <LlmStatusProvider>
        <CreditsBanner />
        <CreditsWarning />
      </LlmStatusProvider>,
    );
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("explains a run cut short by running out of credits", () => {
    render(<RunCreditsNotice errorKind="credits_exhausted" judgeOnly={false} bookings={2} />);
    expect(screen.getByRole("heading")).toHaveTextContent("Out of API credits");
    expect(screen.getByRole("alert")).toHaveTextContent("The 2 bookings made before that are shown below");
  });

  it("distinguishes a missing judge score from an incomplete trip", () => {
    render(<RunCreditsNotice errorKind="credits_exhausted" judgeOnly bookings={4} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The itinerary below is complete");
  });

  it("labels out-of-credit runs in lists", () => {
    render(<StatusBadge status="failed" errorKind="credits_exhausted" />);
    expect(screen.getByText("Out of credits")).toBeInTheDocument();
  });
});

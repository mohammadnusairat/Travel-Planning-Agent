import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OutcomeBadge, StatusBadge } from "./components/ui";
import { humanize, money, parseStep } from "./format";
import { routeProblems } from "./pages/PlanTrip";

describe("parseStep", () => {
  it("splits a ReAct response into thought and action", () => {
    const step = parseStep('THOUGHT: Book the cheapest flight.\nACTION: book_flight(flight_id="f1", party_size=1)');
    expect(step.thought).toBe("Book the cheapest flight.");
    expect(step.action).toBe('book_flight(flight_id="f1", party_size=1)');
  });

  it("handles responses without an action", () => {
    expect(parseStep("# FINAL ITINERARY")).toEqual({ thought: "# FINAL ITINERARY", action: null });
  });
});

describe("routeProblems", () => {
  const routes = new Set(["Detroit→Chicago", "Chicago→Detroit", "Chicago→Miami"]);

  it("accepts a routable round trip", () => {
    expect(routeProblems("Detroit", [{ city: "Chicago", nights: 2 }], routes)).toEqual([]);
  });

  it("reports legs whose flights don't run on that day", () => {
    const days = new Map([["Detroit→Chicago", ["friday", "saturday", "sunday"]], ["Chicago→Detroit", ["sunday", "monday"]]]);
    // Sunday 2026-11-08 out, back Tuesday 2026-11-10: the return doesn't operate on Tuesdays
    expect(routeProblems("Detroit", [{ city: "Chicago", nights: 2 }], routes, days, "2026-11-08")).toEqual([
      "Chicago → Detroit doesn't fly on Tuesday 2026-11-10; it only flies on Sundays, Mondays.",
    ]);
    expect(routeProblems("Detroit", [{ city: "Chicago", nights: 1 }], routes, days, "2026-11-08")).toEqual([]);
  });

  it("reports legs with no direct flight, including the way home", () => {
    expect(routeProblems("Detroit", [{ city: "Chicago", nights: 2 }, { city: "Miami", nights: 2 }], routes)).toEqual([
      "No direct flight from Miami to Detroit.",
    ]);
  });
});

describe("formatting", () => {
  it("formats money and requirement names", () => {
    expect(money(1234.5)).toBe("$1,234.50");
    expect(money(null)).toBe("—");
    expect(humanize("accessible_restaurants_min_5")).toBe("Accessible restaurants min. 5");
  });
});

describe("badges", () => {
  it("shows run status and outcome", () => {
    render(
      <>
        <StatusBadge status="running" />
        <OutcomeBadge success={1} status="completed" />
        <OutcomeBadge success={0} status="running" />
      </>,
    );
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Success")).toBeInTheDocument();
    expect(screen.queryByText("Not met")).not.toBeInTheDocument();
  });
});

"""
server/trips.py - Inventory catalog and custom-trip -> task translation.

A custom trip from the web form becomes a task in the same schema as the
benchmark tasks, so the agent, constraint engine, and judge treat both alike.
"""

import json
import re
from collections import Counter
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

DISRUPTIONS = {
    "none": "No disruption",
    "hotel_unavailable": "Hotel cancels the reservation mid-planning",
    "budget_cut": "Budget is cut by 15% mid-planning",
}


class Destination(BaseModel):
    city: str
    nights: int = Field(ge=1, le=21)


class CustomTrip(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    origin_city: str
    destinations: List[Destination] = Field(min_length=1, max_length=4)
    departure_date: date
    budget_max: float = Field(gt=0, le=100_000)
    party_size: int = Field(ge=1, le=12)
    interests: List[str] = Field(default_factory=list, max_length=10)
    preferences: List[str] = Field(default_factory=list, max_length=10)
    wheelchair_accessible: bool = False
    min_activities: int = Field(default=2, ge=0, le=20)
    min_restaurants: int = Field(default=2, ge=0, le=20)
    disruption: Literal["none", "hotel_unavailable", "budget_cut"] = "none"

    @field_validator("interests", "preferences")
    @classmethod
    def _clean_terms(cls, values: List[str]) -> List[str]:
        return [v.strip() for v in values if v and v.strip()]

    @model_validator(mode="after")
    def _distinct_stops(self) -> "CustomTrip":
        cities = [d.city.lower() for d in self.destinations]
        if self.origin_city.lower() in cities:
            raise ValueError("Destinations must differ from the origin city.")
        if len(set(cities)) != len(cities):
            raise ValueError("Each destination can appear only once.")
        return self


class Catalog:
    """Read-only view over the mock inventory for building and validating trips."""

    def __init__(self, mock_data_dir: Path):
        load = lambda name, key: json.loads((mock_data_dir / f"{name}.json").read_text(encoding="utf-8"))[key]
        self.hotels = load("hotels", "hotels")
        self.activities = load("activities", "activities")
        self.restaurants = load("restaurants", "restaurants")
        self.flights = load("flights", "flights")
        self.routes: Set[Tuple[str, str]] = {(f["from_city"], f["to_city"]) for f in self.flights}

    def destination_cities(self) -> List[str]:
        return sorted({h["city"] for h in self.hotels})

    def origin_cities(self) -> List[str]:
        return sorted({origin for origin, _ in self.routes})

    def canonical_city(self, name: str) -> Optional[str]:
        known = {c.lower(): c for c in self.origin_cities() + self.destination_cities()}
        return known.get(name.strip().lower())

    def describe(self) -> Dict[str, Any]:
        tag_counts = Counter(tag for a in self.activities for tag in a.get("tags", []))
        tag_counts.update(a["type"] for a in self.activities)
        cities = []
        for city in self.destination_cities():
            cities.append({
                "name": city,
                "hotels": sum(1 for h in self.hotels if h["city"] == city),
                "activities": sum(1 for a in self.activities if a["city"] == city),
                "restaurants": sum(1 for r in self.restaurants if r["city"] == city),
                "accessible_hotels": sum(1 for h in self.hotels if h["city"] == city and h["wheelchair_accessible"]),
            })
        return {
            "origins": self.origin_cities(),
            "destinations": cities,
            "routes": sorted([list(route) for route in self.routes]),
            "interest_suggestions": [tag for tag, _ in tag_counts.most_common(40)],
            "disruptions": DISRUPTIONS,
        }

    def validate_trip(self, trip: CustomTrip) -> List[str]:
        """Problems that would make the trip impossible to plan with this inventory."""
        problems = []
        origin = self.canonical_city(trip.origin_city)
        if origin is None or origin not in self.origin_cities():
            problems.append(f"No flights depart from '{trip.origin_city}'.")
        stops = []
        for destination in trip.destinations:
            city = self.canonical_city(destination.city)
            if city is None or city not in self.destination_cities():
                problems.append(f"'{destination.city}' has no hotels in the inventory.")
            stops.append(city or destination.city)
        if problems:
            return problems
        legs = list(zip([origin] + stops, stops + [origin]))
        for start, end in legs:
            if (start, end) not in self.routes:
                problems.append(f"No direct flight from {start} to {end}.")
        return problems


def slug(city: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", city.lower()).strip("_")


def build_task(trip: CustomTrip, catalog: Catalog, task_id: str) -> Dict[str, Any]:
    origin = catalog.canonical_city(trip.origin_city) or trip.origin_city
    stops = [(catalog.canonical_city(d.city) or d.city, d.nights) for d in trip.destinations]
    total_nights = sum(nights for _, nights in stops)
    return_date = trip.departure_date + timedelta(days=total_nights)
    multi_city = len(stops) > 1

    required: List[str] = []
    if multi_city:
        # Explicit legs so the agent books the inter-city flights, not just out and back.
        required.append("outbound_flight")
        for (a, _), (b, _) in zip(stops, stops[1:]):
            required.append(f"mid_trip_flight_{slug(a)}_to_{slug(b)}")
        required.append("return_flight")
        for city, nights in stops:
            required.append(f"hotel_{slug(city)}_{nights}_nights")
    else:
        hotel = f"hotel_{stops[0][1]}_nights"
        required.append(f"{hotel}_wheelchair_accessible" if trip.wheelchair_accessible else hotel)
    if trip.min_activities:
        required.append(f"activities_min_{trip.min_activities}")
    if trip.min_restaurants:
        prefix = "accessible_" if trip.wheelchair_accessible else ""
        required.append(f"{prefix}restaurants_min_{trip.min_restaurants}")

    success_criteria: Dict[str, Any] = {"total_cost_max": trip.budget_max, "timing_feasible": True}
    if trip.wheelchair_accessible:
        success_criteria["all_venues_wheelchair_accessible"] = True

    dynamic_events: List[Dict[str, Any]] = []
    if trip.disruption == "hotel_unavailable":
        first_city = stops[0][0]
        dynamic_events.append({
            "event_id": "event_1",
            "trigger_turn": 5,
            "event_type": "accommodation_unavailable",
            "description": f"Your hotel in {first_city} just cancelled your reservation because it is overbooked. "
                           "Find an alternative hotel for the same nights.",
            "affected_components": [f"{slug(first_city)}_hotel" if multi_city else "hotel"],
            "resolution_requirements": [
                f"Book an alternative hotel in {first_city} for the same nights",
                "Stay within the budget",
                "Keep unaffected bookings",
            ],
        })
        success_criteria["replanning_successful"] = True
    elif trip.disruption == "budget_cut":
        new_budget = round(trip.budget_max * 0.85, 2)
        dynamic_events.append({
            "event_id": "event_1",
            "trigger_turn": 6,
            "event_type": "budget_reduced",
            "description": f"An unexpected expense came up. Your maximum budget is now ${new_budget:.0f}. "
                           "Cancel or downgrade bookings until the trip fits.",
            "affected_components": [],
            "resolution_requirements": [f"Total cost must not exceed ${new_budget:.0f}", "Keep all required components"],
        })
        success_criteria["total_cost_max"] = new_budget
        success_criteria["replanning_successful"] = True

    route = " → ".join([origin] + [city for city, _ in stops] + [origin])
    return {
        "task_id": task_id,
        "difficulty": "custom",
        "title": trip.title or f"{route} ({total_nights} nights)",
        "scenario": {
            "description": f"Custom trip: {route}",
            "origin_city": origin,
            "destination_cities": [city for city, _ in stops],
            "trip_duration_days": total_nights,
        },
        "user_profile": {
            "party_size": trip.party_size,
            "traveler_types": ["adult"] * trip.party_size,
            "special_needs": ["wheelchair_accessible"] if trip.wheelchair_accessible else [],
        },
        "initial_constraints": {
            "hard": {
                "budget_max": trip.budget_max,
                "departure_date": trip.departure_date.isoformat(),
                "return_date": return_date.isoformat(),
                **({"wheelchair_accessible": True} if trip.wheelchair_accessible else {}),
            },
            "soft": {"interests": trip.interests, "preferences": trip.preferences},
        },
        "dynamic_events": dynamic_events,
        "required_components": required,
        "success_criteria": success_criteria,
    }


@lru_cache(maxsize=None)
def _task_files(tasks_dir: str) -> Tuple[Path, ...]:
    files = Path(tasks_dir).glob("*/*.json")
    return tuple(sorted(files, key=lambda p: (["easy", "medium", "hard"].index(p.parent.name)
                                              if p.parent.name in ("easy", "medium", "hard") else 9,
                                              len(p.stem), p.stem)))


def list_benchmark_tasks(tasks_dir: Path) -> List[Dict[str, Any]]:
    tasks = []
    for path in _task_files(str(tasks_dir)):
        task = json.loads(path.read_text(encoding="utf-8"))
        scenario = task.get("scenario", {})
        tasks.append({
            "name": path.stem,
            "task_id": task.get("task_id"),
            "difficulty": task.get("difficulty", path.parent.name),
            "title": task.get("title", ""),
            "origin_city": scenario.get("origin_city"),
            "destination_cities": scenario.get("destination_cities", []),
            "days": scenario.get("trip_duration_days"),
            "budget_max": task.get("initial_constraints", {}).get("hard", {}).get("budget_max"),
            "party_size": task.get("user_profile", {}).get("party_size"),
            "dynamic_events": [e.get("event_type") for e in task.get("dynamic_events", [])],
        })
    return tasks


def load_benchmark_task(tasks_dir: Path, name: str) -> Optional[Dict[str, Any]]:
    for path in _task_files(str(tasks_dir)):
        if path.stem == name:
            return json.loads(path.read_text(encoding="utf-8"))
    return None

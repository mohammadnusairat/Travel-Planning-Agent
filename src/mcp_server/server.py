"""
src/mcp_server/server.py - MCP server exposing the travel tool suite.

The agent never touches the mock-data tool classes directly; it discovers and
calls these tools over the Model Context Protocol (stdio by default).

Run standalone:
    python -m src.mcp_server.server

Every tool returns a JSON string so result shapes are identical to the
underlying tool classes (lists stay lists, dicts stay dicts).
"""

import json
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from src.tools.activities import ActivitySearchTool
from src.tools.flights import FlightSearchTool
from src.tools.hotels import HotelSearchTool
from src.tools.restaurants import RestaurantSearchTool

MOCK_DATA_DIR = PROJECT_ROOT / "benchmarks" / "mock_data"

TOOL_NAMES = [
    # flights
    "search_flights",
    "find_connecting_flights",
    "get_flight_details",
    "book_flight",
    "cancel_flight",
    # hotels
    "search_hotels",
    "get_hotel_details",
    "book_hotel",
    "cancel_hotel",
    # restaurants
    "search_restaurants",
    "get_restaurant_details",
    "book_restaurant",
    "cancel_restaurant",
    # activities
    "search_activities",
    "get_activity_details",
    "book_activity",
    "cancel_activity",
    # cross-cutting utilities
    "check_availability",
    "get_destination_overview",
    "record_surcharge",
]


def _dump(payload: Any) -> str:
    return json.dumps(payload, default=str)


def _find(items: List[dict], key: str, value: str) -> Optional[dict]:
    for item in items:
        if item.get(key) == value:
            return item
    return None


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _within_hours(time: str, open_time: str, close_time: str) -> bool:
    if open_time <= close_time:
        return open_time <= time <= close_time
    return time >= open_time or time <= close_time  # overnight window, e.g. 20:00-02:00


def build_server(data_dir: Path = MOCK_DATA_DIR) -> FastMCP:
    flights = FlightSearchTool(str(data_dir / "flights.json"))
    hotels = HotelSearchTool(str(data_dir / "hotels.json"))
    restaurants = RestaurantSearchTool(str(data_dir / "restaurants.json"))
    activities = ActivitySearchTool(str(data_dir / "activities.json"))

    server = FastMCP("travel-tools", log_level="WARNING")

    # ------------------------------------------------------------------ flights

    @server.tool()
    def search_flights(
        original_city: str,
        destination_city: str,
        departure_date: str,
        return_date: Optional[str] = None,
        departure_time_earliest: Optional[str] = None,
        return_time_latest: Optional[str] = None,
        max_price: Optional[float] = None,
        wheelchair_accessible: Optional[bool] = None,
    ) -> str:
        """Find direct outbound (and optional return) flights between two cities, sorted by price."""
        return _dump(flights.search(
            original_city=original_city,
            destination_city=destination_city,
            departure_date=departure_date,
            return_date=return_date,
            departure_time_earliest=departure_time_earliest,
            return_time_latest=return_time_latest,
            max_price=max_price,
            wheelchair_accessible=wheelchair_accessible,
        ))

    @server.tool()
    def find_connecting_flights(
        original_city: str,
        destination_city: str,
        departure_date: str,
        min_layover_minutes: int = 60,
        max_total_price: Optional[float] = None,
    ) -> str:
        """Find direct and one-stop routings between two cities on a date (multi-city fallback when a leg is cancelled)."""
        day = datetime.strptime(departure_date, "%Y-%m-%d").strftime("%A").lower()
        operating = [f for f in flights.flights if day in f["days_available"]]
        origin, destination = original_city.lower(), destination_city.lower()

        routings = []
        for leg in operating:
            if leg["from_city"].lower() == origin and leg["to_city"].lower() == destination:
                routings.append({"stops": 0, "legs": [leg["flight_id"]], "total_price_per_seat": leg["price_per_seat"],
                                 "departure_time": leg["departure_time"], "arrival_time": leg["arrival_time"]})

        for first in operating:
            if first["from_city"].lower() != origin or first["to_city"].lower() == destination:
                continue
            hub = first["to_city"].lower()
            for second in operating:
                if second["from_city"].lower() != hub or second["to_city"].lower() != destination:
                    continue
                layover = _minutes(second["departure_time"]) - _minutes(first["arrival_time"])
                if layover < min_layover_minutes:
                    continue
                routings.append({
                    "stops": 1,
                    "via": first["to_city"],
                    "legs": [first["flight_id"], second["flight_id"]],
                    "layover_minutes": layover,
                    "total_price_per_seat": first["price_per_seat"] + second["price_per_seat"],
                    "departure_time": first["departure_time"],
                    "arrival_time": second["arrival_time"],
                })

        if max_total_price is not None:
            routings = [r for r in routings if r["total_price_per_seat"] <= max_total_price]
        routings.sort(key=lambda r: (r["stops"], r["total_price_per_seat"]))
        return _dump({"date": departure_date, "routings": routings[:10]})

    @server.tool()
    def get_flight_details(flight_id: str) -> str:
        """Return the full record for one flight (schedule, days operated, seats, accessibility)."""
        flight = _find(flights.flights, "flight_id", flight_id)
        return _dump(flight or {"status": "error", "message": f"Flight with ID '{flight_id}' not found."})

    @server.tool()
    def book_flight(
        flight_id: str,
        outbound: bool,
        origin_city: str,
        destination_city: str,
        departure_date: str,
        party_size: int,
    ) -> str:
        """Book a specific flight for the party. Returns a bk_ booking ID and total cost."""
        return _dump(flights.book(
            flight_id=flight_id,
            outbound=outbound,
            origin_city=origin_city,
            destination_city=destination_city,
            departure_date=departure_date,
            party_size=party_size,
        ))

    @server.tool()
    def cancel_flight(booking_id: str) -> str:
        """Cancel a flight booking by its bk_ booking ID."""
        return _dump(flights.cancel(booking_id))

    # ------------------------------------------------------------------- hotels

    @server.tool()
    def search_hotels(
        city: str,
        max_price: Optional[float] = None,
        wheelchair_accessible: Optional[bool] = None,
    ) -> str:
        """Find hotels in a city, sorted by nightly price."""
        return _dump(hotels.search(city=city, max_price=max_price, wheelchair_accessible=wheelchair_accessible))

    @server.tool()
    def get_hotel_details(hotel_id: str) -> str:
        """Return the full record for one hotel (room types, amenities, proximity to attractions)."""
        hotel = _find(hotels.hotels, "hotel_id", hotel_id)
        return _dump(hotel or {"status": "error", "message": f"Hotel with ID '{hotel_id}' not found."})

    @server.tool()
    def book_hotel(
        hotel_id: str,
        check_in: str,
        check_out: str,
        party_size: int,
        num_rooms: int = 1,
    ) -> str:
        """Book a hotel stay. Cost = nightly price x nights x rooms."""
        return _dump(hotels.book(
            hotel_id=hotel_id,
            check_in=check_in,
            check_out=check_out,
            party_size=party_size,
            num_rooms=num_rooms,
        ))

    @server.tool()
    def cancel_hotel(booking_id: str) -> str:
        """Cancel a hotel booking by its bk_ booking ID."""
        return _dump(hotels.cancel(booking_id))

    # -------------------------------------------------------------- restaurants

    @server.tool()
    def search_restaurants(
        city: str,
        special_needs: Optional[List[str]] = None,
        interests: Optional[List[str]] = None,
        preferences: Optional[List[str]] = None,
        max_price: Optional[float] = None,
        party_size: Optional[int] = None,
        target_date: Optional[str] = None,
        start_time: Optional[str] = None,
        wheelchair_accessible: Optional[bool] = None,
    ) -> str:
        """Find restaurants in a city, ranked by interest match then price."""
        return _dump(restaurants.search(
            city=city,
            special_needs=special_needs,
            interests=interests,
            preferences=preferences,
            max_price=max_price,
            party_size=party_size,
            target_date=target_date,
            start_time=start_time,
            wheelchair_accessible=wheelchair_accessible,
        ))

    @server.tool()
    def get_restaurant_details(restaurant_id: str) -> str:
        """Return the full record for one restaurant (opening hours, dietary options, group size)."""
        restaurant = _find(restaurants.restaurants, "restaurant_id", restaurant_id)
        return _dump(restaurant or {"status": "error", "message": f"Restaurant with ID '{restaurant_id}' not found."})

    @server.tool()
    def book_restaurant(restaurant_id: str, date: str, time: str, party_size: int) -> str:
        """Reserve a restaurant table. Cost = average cost per person x party size."""
        return _dump(restaurants.book(restaurant_id=restaurant_id, date=date, time=time, party_size=party_size))

    @server.tool()
    def cancel_restaurant(booking_id: str) -> str:
        """Cancel a restaurant booking by its bk_ booking ID."""
        return _dump(restaurants.cancel(booking_id))

    # --------------------------------------------------------------- activities

    @server.tool()
    def search_activities(
        city: str,
        interests: Optional[List[str]] = None,
        preferences: Optional[List[str]] = None,
        max_price: Optional[float] = None,
        party_size: Optional[int] = None,
        target_date: Optional[str] = None,
        start_time: Optional[str] = None,
        wheelchair_accessible: Optional[bool] = None,
    ) -> str:
        """Find activities and attractions in a city, sorted by price."""
        return _dump(activities.search(
            city=city,
            interests=interests,
            preferences=preferences,
            max_price=max_price,
            party_size=party_size,
            target_date=target_date,
            start_time=start_time,
            wheelchair_accessible=wheelchair_accessible,
        ))

    @server.tool()
    def get_activity_details(activity_id: str) -> str:
        """Return the full record for one activity (duration, operating hours, days available, group limits)."""
        activity = _find(activities.activities, "activity_id", activity_id)
        return _dump(activity or {"status": "error", "message": f"Activity with ID '{activity_id}' not found."})

    @server.tool()
    def book_activity(activity_id: str, date: str, time: str, party_size: int) -> str:
        """Book an activity slot. Cost = price per person x party size."""
        return _dump(activities.book(activity_id=activity_id, date=date, time=time, party_size=party_size))

    @server.tool()
    def cancel_activity(booking_id: str) -> str:
        """Cancel an activity booking by its bk_ booking ID."""
        return _dump(activities.cancel(booking_id))

    # ---------------------------------------------------------------- utilities

    @server.tool()
    def check_availability(
        item_type: str,
        item_id: str,
        date: str,
        time: Optional[str] = None,
        party_size: int = 1,
    ) -> str:
        """Check whether a flight, hotel, restaurant, or activity can be booked on a date/time for a party, without booking it."""
        try:
            day = datetime.strptime(date, "%Y-%m-%d").strftime("%A")
        except ValueError:
            return _dump({"available": False, "reasons": ["Invalid date format. Use YYYY-MM-DD."]})

        reasons: List[str] = []
        item_type = item_type.lower().rstrip("s")
        if item_type == "flight":
            item = _find(flights.flights, "flight_id", item_id)
            if item:
                if day.lower() not in item["days_available"]:
                    reasons.append(f"Does not operate on {day}.")
                if party_size > min(item["seats_available"], item["max_group_size"]):
                    reasons.append("Not enough seats for the party in one booking.")
        elif item_type == "hotel":
            item = _find(hotels.hotels, "hotel_id", item_id)
            if item and party_size > item.get("max_guests", 2):
                reasons.append(f"Party of {party_size} needs more than one room (max {item.get('max_guests', 2)} per room).")
        elif item_type == "restaurant":
            item = _find(restaurants.restaurants, "restaurant_id", item_id)
            if item:
                hours = item["opening_hours"].get(day, "Closed")
                if hours == "Closed":
                    reasons.append(f"Closed on {day}.")
                elif time:
                    open_time, close_time = hours.split("-")
                    if not _within_hours(time, open_time, close_time):
                        reasons.append(f"{time} is outside opening hours ({hours}).")
                if party_size > item["max_group_size"]:
                    reasons.append(f"Party exceeds max group size ({item['max_group_size']}).")
        elif item_type == "activity":
            item = _find(activities.activities, "activity_id", item_id)
            if item:
                if day.lower() not in item["days_available"]:
                    reasons.append(f"Not offered on {day}.")
                if time:
                    hours = item["operating_hours"]
                    if not _within_hours(time, hours["open"], hours["close"]):
                        reasons.append(f"{time} is outside operating hours ({hours['open']}-{hours['close']}).")
                    else:
                        end = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M") + timedelta(hours=float(item["duration_hours"]))
                        item = {**item, "ends_at": end.strftime("%Y-%m-%d %H:%M")}
                if not item["min_group"] <= party_size <= item["max_group"]:
                    reasons.append(f"Party size must be between {item['min_group']} and {item['max_group']}.")
        else:
            return _dump({"available": False, "reasons": [f"Unknown item_type '{item_type}'."]})

        if item is None:
            return _dump({"available": False, "reasons": [f"{item_type.capitalize()} '{item_id}' not found."]})
        result = {"available": not reasons, "reasons": reasons, "item_type": item_type, "item_id": item_id, "date": date}
        if "ends_at" in item:
            result["ends_at"] = item["ends_at"]
        return _dump(result)

    @server.tool()
    def get_destination_overview(city: str) -> str:
        """Summarize inventory for a city: counts, price ranges, accessible options, activity types, and direct routes."""
        key = city.lower()
        city_hotels = [h for h in hotels.hotels if h["city"].lower() == key]
        city_restaurants = [r for r in restaurants.restaurants if r["city"].lower() == key]
        city_activities = [a for a in activities.activities if a["city"].lower() == key]

        def price_range(values):
            return [min(values), max(values)] if values else None

        return _dump({
            "city": city,
            "hotels": {
                "count": len(city_hotels),
                "price_per_night_range": price_range([h["price_per_night"] for h in city_hotels]),
                "wheelchair_accessible": sum(1 for h in city_hotels if h["wheelchair_accessible"]),
            },
            "restaurants": {
                "count": len(city_restaurants),
                "avg_cost_per_person_range": price_range([r["average_cost_per_person"] for r in city_restaurants]),
                "wheelchair_accessible": sum(1 for r in city_restaurants if r["is_wheelchair_accessible"]),
            },
            "activities": {
                "count": len(city_activities),
                "price_per_person_range": price_range([a["price_per_person"] for a in city_activities]),
                "types": sorted({a["type"] for a in city_activities}),
                "wheelchair_accessible": sum(1 for a in city_activities if a["wheelchair_accessible"]),
            },
            "direct_flights_to": sorted({f["to_city"] for f in flights.flights if f["from_city"].lower() == key}),
            "direct_flights_from": sorted({f["from_city"] for f in flights.flights if f["to_city"].lower() == key}),
        })

    @server.tool()
    def record_surcharge(amount: float, description: str) -> str:
        """Record a mandatory fee or penalty imposed by a disruption (e.g. a rebooking fee) so it counts against the budget."""
        if amount < 0:
            return _dump({"status": "error", "message": "Surcharge amount must be non-negative."})
        booking_id = f"bk_surcharge_{uuid.uuid4().hex[:10]}"
        return _dump({
            "status": "success",
            "booking_id": booking_id,
            "cost": float(amount),
            "details": {"name": description},
            "message": f"Surcharge of ${amount} recorded successfully.",
        })

    return server


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()

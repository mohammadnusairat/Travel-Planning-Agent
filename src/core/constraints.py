from datetime import date, datetime, time as dt_time, timedelta
from typing import List, Dict, Any, Optional, Tuple
from src.utils.types import Constraint, Booking, ConstraintViolation

TimeWindow = Tuple[datetime, datetime]

# Temporal rules
POST_ARRIVAL_BUFFER = timedelta(minutes=90)     # nothing at the destination until 90 min after landing
PRE_DEPARTURE_BUFFER = timedelta(minutes=120)   # everything must end 120 min before take-off
RESTAURANT_DURATION = timedelta(minutes=90)
DEFAULT_ACTIVITY_HOURS = 2.0
UNTIMED_ACTIVITY_TYPES = ("transportation", "rental")  # all-day rentals don't occupy a time slot


class ConstraintTracker:
    """
    Tracks constraints, bookings, budget, and dependencies.

    This class is responsible for:
    - Storing hard and soft constraints
    - Managing bookings and their costs
    - Tracking budget usage and limits
    - Maintaining a dependency graph for bookings to support replanning
    - Temporal logic: booking time windows, overlaps, flight buffers, trip-date bounds
    - Dependency rules: inferring which bookings rely on which (flight -> hotel -> activities/meals)
    - Validating the whole itinerary and screening candidate bookings before they are committed
    """
    
    def __init__(self):
        """
        Initialize empty tracker
        
        Internal state to maintain:
        - self.constraints: List[Constraint]
        - self.bookings: List[Booking]
        - self.budget_max: Optional[float]
        - self.budget_used: float
        - self.dependency_graph: Dict[str, List[str]]
        """
        self.constraints: List[Constraint] = []
        self.bookings: List[Booking] = []
        self.budget_max: Optional[float] = None
        self.budget_used: float = 0.0
        self.dependency_graph: Dict[str, List[str]] = {}  # booking_id -> list of dependent booking_ids
    
    # ========== CONSTRAINT MANAGEMENT ==========
    
    def add_constraint(self, name: str, value: Any, is_hard: bool = True):
        """
        Add a constraint
        
        Args:
            name: Constraint name (e.g., "budget_max", "wheelchair_accessible")
            value: Constraint value
            is_hard: True = must satisfy, False = preference
            
        Example:
            tracker.add_constraint("budget_max", 1000, is_hard=True)
            tracker.add_constraint("interests", ["hiking"], is_hard=False)
        """
        constraint = Constraint(name=name, value=value, is_hard=is_hard)
        self.constraints.append(constraint)
        
        # Special handling for budget_max
        if name == "budget_max" and isinstance(value, (int, float)):
            self.budget_max = float(value)
    
    def get_constraint(self, name: str) -> Optional[Constraint]:
        """
        Get a constraint by name
        
        Returns:
            Constraint object or None if not found
        """
        for constraint in self.constraints:
            if constraint.name == name:
                return constraint
        return None
    
    def get_hard_constraints(self) -> List[Constraint]:
        """
        Get all hard constraints
        
        Returns:
            List of constraints where is_hard=True
        """
        return [c for c in self.constraints if c.is_hard]
    
    def get_soft_constraints(self) -> List[Constraint]:
        """
        Get all soft preferences
        
        Returns:
            List of constraints where is_hard=False
        """
        return [c for c in self.constraints if not c.is_hard]
    
    # ========== BOOKING MANAGEMENT ==========
    
    def add_booking(self, booking: Booking):
        """
        Add a booking and update budget
        
        Args:
            booking: Booking object with cost
            
        Side effects:
            - Adds booking to self.bookings
            - Updates self.budget_used
            - Registers dependencies in graph
            
        Example:
            flight = Booking(
                booking_id="flight_001",
                type="flight",
                details={"origin": "Chicago", "destination": "Denver"},
                cost=300.0,
                dependencies=[]
            )
            tracker.add_booking(flight)
        """
        if self.get_booking(booking.booking_id) is not None:
            return  # already exists, skip to avoid double-counting

        self.bookings.append(booking)
        self.budget_used += booking.cost

        # Initialize dependency graph entry for this booking
        if booking.booking_id not in self.dependency_graph:
            self.dependency_graph[booking.booking_id] = []

        # Register dependencies in graph
        for depends_on in booking.dependencies:
            if depends_on not in self.dependency_graph:
                self.dependency_graph[depends_on] = []
            if booking.booking_id not in self.dependency_graph[depends_on]:
                self.dependency_graph[depends_on].append(booking.booking_id)
    
    def get_booking(self, booking_id: str) -> Optional[Booking]:
        """
        Get a booking by ID
        
        Returns:
            Booking object or None if not found
        """
        for booking in self.bookings:
            if booking.booking_id == booking_id:
                return booking
        return None
    
    def remove_booking(self, booking_id: str):
        """
        Remove a booking and update budget
        
        Args:
            booking_id: ID of booking to remove
            
        Side effects:
            - Removes booking from self.bookings
            - Updates self.budget_used
            - Cleans up dependency graph
        """
        booking = self.get_booking(booking_id)
        if booking:
            self.bookings.remove(booking)
            self.budget_used -= booking.cost
            
            # Clean up dependency graph
            if booking_id in self.dependency_graph:
                del self.dependency_graph[booking_id]
            
            # Remove this booking from other bookings' dependency lists
            for booking_id_key in self.dependency_graph:
                if booking_id in self.dependency_graph[booking_id_key]:
                    self.dependency_graph[booking_id_key].remove(booking_id)
    
    def update_booking(self, booking_id: str, new_booking: Booking):
        """
        Update an existing booking (for replanning)
        
        Args:
            booking_id: ID of booking to update
            new_booking: New booking details
            
        Side effects:
            - Updates budget (subtract old, add new)
            - Replaces booking in list
        """
        old_booking = self.get_booking(booking_id)
        if old_booking:
            self.budget_used -= old_booking.cost
            self.budget_used += new_booking.cost

            idx = self.bookings.index(old_booking)
            self.bookings[idx] = new_booking

            # Rebuild dependency graph edges for this booking
            # Remove old edges where old booking_id was a dependent
            for depends_on in old_booking.dependencies:
                if depends_on in self.dependency_graph and booking_id in self.dependency_graph[depends_on]:
                    self.dependency_graph[depends_on].remove(booking_id)

            # Add new edges from new booking's dependencies
            for depends_on in new_booking.dependencies:
                if depends_on not in self.dependency_graph:
                    self.dependency_graph[depends_on] = []
                if new_booking.booking_id not in self.dependency_graph[depends_on]:
                    self.dependency_graph[depends_on].append(new_booking.booking_id)
    
    def get_bookings_by_type(self, booking_type: str) -> List[Booking]:
        """
        Get all bookings of a specific type
        
        Args:
            booking_type: "flight", "hotel", "restaurant", "activity"
            
        Returns:
            List of matching bookings
            
        Example:
            flights = tracker.get_bookings_by_type("flight")
        """
        return [b for b in self.bookings if b.type == booking_type]
    
    # ========== BUDGET TRACKING ==========
    
    def get_remaining_budget(self) -> float:
        """
        Get remaining budget
        
        Returns:
            budget_max - budget_used, or inf if no budget limit
        """
        if self.budget_max is None:
            return float('inf')
        return self.budget_max - self.budget_used
    
    def is_within_budget(self, additional_cost: float = 0.0) -> bool:
        """
        Check if within budget
        
        Args:
            additional_cost: Optional cost to check before adding
            
        Returns:
            True if (budget_used + additional_cost) <= budget_max
            
        Example:
            if tracker.is_within_budget(250):
                # Can afford this $250 booking
        """
        if self.budget_max is None:
            return True
        return (self.budget_used + additional_cost) <= self.budget_max
    
    # ========== DEPENDENCY TRACKING ==========
    
    def add_dependency(self, booking_id: str, depends_on: str):
        """
        Add a dependency relationship
        
        Args:
            booking_id: The booking that depends on something
            depends_on: The booking it depends on
            
        Example:
            tracker.add_dependency("hotel_001", "flight_001")
            # Hotel check-in depends on flight arrival
        """
        if depends_on not in self.dependency_graph:
            self.dependency_graph[depends_on] = []
        if booking_id not in self.dependency_graph[depends_on]:
            self.dependency_graph[depends_on].append(booking_id)
    
    def find_dependent_bookings(self, booking_id: str) -> List[str]:
        """
        Find direct dependencies (one level)
        
        Args:
            booking_id: ID of the booking
            
        Returns:
            List of booking IDs that depend on this one
            
        Example:
            deps = tracker.find_dependent_bookings("flight_001")
            # Returns ["hotel_001", "restaurant_001"]

        Includes both explicitly registered dependencies and those inferred
        from the dependency rules (see infer_dependencies).
        """
        dependents = list(self.dependency_graph.get(booking_id, []))
        for booking in self.bookings:
            if booking.booking_id != booking_id and booking.booking_id not in dependents:
                if booking_id in self.infer_dependencies(booking):
                    dependents.append(booking.booking_id)
        return dependents
    
    def find_all_affected_bookings(self, booking_id: str) -> List[str]:
        """
        Find ALL affected bookings recursively (transitive dependencies)
        
        This is CRITICAL for replanning!
        
        Args:
            booking_id: ID of the changed booking
            
        Returns:
            List of ALL affected booking IDs (including chains)
            
        Example:
            # If flight_001 changes:
            # flight_001 -> hotel_001 -> restaurant_001 -> activity_001
            affected = tracker.find_all_affected_bookings("flight_001")
            # Returns ["hotel_001", "restaurant_001", "activity_001"]
        """
        affected = []
        visited = set()
        
        def dfs(current_id: str):
            """Depth-first search to find all transitive dependents"""
            if current_id in visited:
                return
            visited.add(current_id)
            
            # Get direct dependents
            direct_dependents = self.find_dependent_bookings(current_id)
            for dependent_id in direct_dependents:
                if dependent_id not in visited:
                    affected.append(dependent_id)
                    dfs(dependent_id)
        
        dfs(booking_id)
        return affected

    # ========== DEPENDENCY RULES ==========

    def infer_dependencies(self, booking: Booking) -> List[str]:
        """
        Derive the bookings this booking relies on from itinerary structure.

        Rules:
            hotel in city C            -> the flight arriving in C for that stay and the flight leaving C after it
            activity/meal on day D in C -> the hotel in C covering D, plus any flight landing in or leaving C on D
            flights, surcharges        -> roots (no inferred dependencies)
        """
        details = booking.details or {}
        dependencies: List[str] = []

        if booking.type == "hotel":
            city = self.booking_city(booking)
            check_in = self.parse_date(str(details.get("check_in", "")))
            if not city or check_in is None:
                return dependencies
            inbound = [
                f for f in self.get_bookings_by_type("flight")
                if self._flight_city(f, "destination_city") == city
                and (self._flight_date(f) or date.max) <= check_in
            ]
            outbound = [
                f for f in self.get_bookings_by_type("flight")
                if self._flight_city(f, "origin_city") == city
                and (self._flight_date(f) or date.min) >= check_in
            ]
            if inbound:
                dependencies.append(max(inbound, key=self._flight_sort_key).booking_id)
            if outbound:
                dependencies.append(min(outbound, key=self._flight_sort_key).booking_id)
            return dependencies

        if booking.type in {"activity", "restaurant"}:
            city = self.booking_city(booking)
            day = self.parse_date(str(details.get("date", "")))
            if day is None:
                return dependencies
            for hotel in self.get_bookings_by_type("hotel"):
                if city and self.booking_city(hotel) not in {None, city}:
                    continue
                hotel_details = hotel.details or {}
                check_in = self.parse_date(str(hotel_details.get("check_in", "")))
                check_out = self.parse_date(str(hotel_details.get("check_out", "")))
                if check_in and check_out and check_in <= day <= check_out:
                    dependencies.append(hotel.booking_id)
            for flight in self.get_bookings_by_type("flight"):
                if self._flight_date(flight) != day:
                    continue
                if city is None or city in {
                    self._flight_city(flight, "origin_city"),
                    self._flight_city(flight, "destination_city"),
                }:
                    dependencies.append(flight.booking_id)
        return dependencies

    def get_dependencies(self, booking_id: str) -> List[str]:
        """Everything a booking depends on: explicit dependencies plus inferred ones."""
        booking = self.get_booking(booking_id)
        if booking is None:
            return []
        combined = list(booking.dependencies)
        for dependency in self.infer_dependencies(booking):
            if dependency not in combined:
                combined.append(dependency)
        return combined

    # ========== TEMPORAL LOGIC ==========

    @staticmethod
    def parse_date(value: str) -> Optional[date]:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None

    @staticmethod
    def parse_time(value: str) -> Optional[dt_time]:
        if not value:
            return None
        for fmt in ("%H:%M", "%I:%M %p", "%I %p"):
            try:
                return datetime.strptime(value.strip(), fmt).time()
            except ValueError:
                continue
        return None

    @staticmethod
    def windows_overlap(left: TimeWindow, right: TimeWindow) -> bool:
        return left[0] < right[1] and right[0] < left[1]

    @staticmethod
    def booking_city(booking: Booking) -> Optional[str]:
        details = booking.details or {}
        full_data = details.get("full_data") if isinstance(details.get("full_data"), dict) else {}
        city = full_data.get("city") or details.get("city")
        return str(city).strip().lower() if city else None

    def _flight_city(self, flight: Booking, key: str) -> Optional[str]:
        value = (flight.details or {}).get(key)
        return str(value).strip().lower() if value else None

    def _flight_date(self, flight: Booking) -> Optional[date]:
        return self.parse_date(str((flight.details or {}).get("departure_date", "")))

    def _flight_sort_key(self, flight: Booking) -> Tuple[date, str]:
        details = flight.details or {}
        return (self._flight_date(flight) or date.min, str(details.get("departure_time", "")))

    def time_window(self, booking_type: str, details: Dict[str, Any]) -> Optional[TimeWindow]:
        """The [start, end) interval a booking occupies, or None if it has no fixed slot."""
        details = details or {}
        full_data = details.get("full_data") if isinstance(details.get("full_data"), dict) else {}

        if booking_type in {"activity", "restaurant"}:
            if booking_type == "activity":
                activity_type = str(full_data.get("type", details.get("type", ""))).lower()
                if any(marker in activity_type for marker in UNTIMED_ACTIVITY_TYPES):
                    return None
            booking_date = self.parse_date(str(details.get("date", "")))
            booking_time = self.parse_time(str(details.get("time", "")))
            if booking_date is None or booking_time is None:
                return None
            start = datetime.combine(booking_date, booking_time)
            if booking_type == "restaurant":
                return start, start + RESTAURANT_DURATION
            duration = full_data.get("duration_hours") or DEFAULT_ACTIVITY_HOURS
            return start, start + timedelta(hours=float(duration))

        if booking_type == "flight":
            departure_date = self.parse_date(str(details.get("departure_date", "")))
            departure_time = self.parse_time(str(details.get("departure_time", "")))
            arrival_time = self.parse_time(str(details.get("arrival_time", "")))
            if departure_date is None or departure_time is None or arrival_time is None:
                return None
            start = datetime.combine(departure_date, departure_time)
            end = datetime.combine(departure_date, arrival_time)
            if end < start:
                end += timedelta(days=1)  # overnight flight
            return start, end

        return None

    def booking_time_window(self, booking: Booking) -> Optional[TimeWindow]:
        return self.time_window(booking.type, booking.details)

    def find_overlapping_booking(
        self,
        window: TimeWindow,
        exclude_ids: Tuple[str, ...] = (),
    ) -> Optional[Tuple[Booking, TimeWindow]]:
        """First committed booking whose time window overlaps `window` (anti-concurrency rule)."""
        for booking in self.bookings:
            if booking.booking_id in exclude_ids:
                continue
            other = self.booking_time_window(booking)
            if other is not None and self.windows_overlap(window, other):
                return booking, other
        return None

    def find_flight_buffer_conflict(
        self,
        window: TimeWindow,
        city: Optional[str] = None,
    ) -> Optional[Booking]:
        """
        Flight whose airport buffer `window` violates.

        Outbound flights: nothing may start before arrival + 90 min on the arrival day.
        Return flights: everything must end by departure - 120 min on the departure day.
        Mid-trip flights: applies the arrival rule in the destination city and the departure
        rule in the origin city (when the booking's city is known).
        """
        for flight in self.get_bookings_by_type("flight"):
            flight_window = self.booking_time_window(flight)
            if flight_window is None or window[0].date() != flight_window[0].date():
                continue
            flight_type = str((flight.details or {}).get("type", "")).lower()
            arrival_rule = departure_rule = False
            if flight_type == "outbound_flight":
                arrival_rule = True
            elif flight_type == "return_flight":
                departure_rule = True
            elif flight_type == "mid_trip_flight" and city:
                arrival_rule = city == self._flight_city(flight, "destination_city")
                departure_rule = city == self._flight_city(flight, "origin_city")
            if arrival_rule and window[0] < flight_window[1] + POST_ARRIVAL_BUFFER:
                return flight
            if departure_rule and window[1] > flight_window[0] - PRE_DEPARTURE_BUFFER:
                return flight
        return None

    def trip_dates(self) -> Tuple[Optional[date], Optional[date]]:
        departure = self.get_constraint("departure_date")
        return_date = self.get_constraint("return_date")
        return (
            self.parse_date(str(departure.value)) if departure and departure.value else None,
            self.parse_date(str(return_date.value)) if return_date and return_date.value else None,
        )

    def outside_trip_dates(self, booking_type: str, details: Dict[str, Any]) -> Optional[str]:
        """Describe how a booking falls outside the trip's departure/return dates, if it does."""
        start, end = self.trip_dates()
        if start is None and end is None:
            return None
        details = details or {}
        if booking_type == "hotel":
            first = self.parse_date(str(details.get("check_in", "")))
            last = self.parse_date(str(details.get("check_out", "")))
        elif booking_type == "flight":
            first = last = self.parse_date(str(details.get("departure_date", "")))
        elif booking_type in {"activity", "restaurant"}:
            first = last = self.parse_date(str(details.get("date", "")))
        else:
            return None
        if first is not None and start is not None and first < start:
            return f"starts {first} before the trip departs on {start}"
        if last is not None and end is not None and last > end:
            return f"ends {last} after the trip returns on {end}"
        return None

    # ========== VALIDATION ==========

    def check_budget(self, additional_cost: float = 0.0) -> Optional[ConstraintViolation]:
        if self.is_within_budget(additional_cost):
            return None
        over = self.budget_used + additional_cost - (self.budget_max or 0.0)
        return ConstraintViolation(
            rule="budget_exceeded",
            category="budget",
            message=(
                f"Total ${self.budget_used + additional_cost:.2f} exceeds the ${self.budget_max:.2f} budget "
                f"by ${over:.2f}."
            ),
        )

    def check_candidate(
        self,
        booking_type: str,
        details: Dict[str, Any],
        cost: Optional[float] = None,
    ) -> List[ConstraintViolation]:
        """Screen a booking before it is committed: budget, overlaps, flight buffers, trip dates."""
        violations: List[ConstraintViolation] = []

        if cost is not None:
            budget_violation = self.check_budget(cost)
            if budget_violation:
                violations.append(budget_violation)

        outside = self.outside_trip_dates(booking_type, details)
        if outside:
            violations.append(ConstraintViolation(
                rule="outside_trip_dates", category="temporal",
                message=f"This {booking_type} {outside}.",
            ))

        window = self.time_window(booking_type, details)
        if window is None:
            return violations

        overlap = self.find_overlapping_booking(window)
        if overlap:
            other, other_window = overlap
            violations.append(ConstraintViolation(
                rule="time_overlap", category="temporal",
                message=(
                    f"Overlaps with {other.type} {other.booking_id} "
                    f"({self.format_window(other_window)}). "
                    f"Choose a start time at or after {other_window[1].strftime('%H:%M')}."
                ),
                booking_ids=[other.booking_id],
            ))

        if booking_type in {"activity", "restaurant"}:
            full_data = (details or {}).get("full_data") if isinstance((details or {}).get("full_data"), dict) else {}
            city = full_data.get("city") or (details or {}).get("city")
            flight = self.find_flight_buffer_conflict(window, str(city).strip().lower() if city else None)
            if flight:
                violations.append(ConstraintViolation(
                    rule="flight_buffer", category="temporal",
                    message=(
                        f"Violates the airport buffer around flight {flight.booking_id} "
                        f"(90 min after arrival / 120 min before departure). "
                        f"Candidate window: {self.format_window(window)}."
                    ),
                    booking_ids=[flight.booking_id],
                ))
        return violations

    def accessibility_required(self) -> bool:
        return any(
            c.is_hard and c.name in {"wheelchair_accessible", "accessibility_required"} and bool(c.value)
            for c in self.constraints
        )

    def validate_itinerary(self) -> List[ConstraintViolation]:
        """Check every committed booking against the budget, temporal, dependency, and accessibility rules."""
        violations: List[ConstraintViolation] = []

        budget_violation = self.check_budget()
        if budget_violation:
            violations.append(budget_violation)

        windows = [(b, w) for b in self.bookings if (w := self.booking_time_window(b)) is not None]
        for index, (left, left_window) in enumerate(windows):
            for right, right_window in windows[index + 1:]:
                if self.windows_overlap(left_window, right_window):
                    violations.append(ConstraintViolation(
                        rule="time_overlap", category="temporal",
                        message=(
                            f"{left.type} {left.booking_id} ({self.format_window(left_window)}) overlaps "
                            f"{right.type} {right.booking_id} ({self.format_window(right_window)})."
                        ),
                        booking_ids=[left.booking_id, right.booking_id],
                    ))

        for booking, window in windows:
            if booking.type not in {"activity", "restaurant"}:
                continue
            flight = self.find_flight_buffer_conflict(window, self.booking_city(booking))
            if flight:
                violations.append(ConstraintViolation(
                    rule="flight_buffer", category="temporal",
                    message=f"{booking.type} {booking.booking_id} is inside the airport buffer of flight {flight.booking_id}.",
                    booking_ids=[booking.booking_id, flight.booking_id],
                ))

        for booking in self.bookings:
            outside = self.outside_trip_dates(booking.type, booking.details)
            if outside:
                violations.append(ConstraintViolation(
                    rule="outside_trip_dates", category="temporal",
                    message=f"{booking.type} {booking.booking_id} {outside}.",
                    booking_ids=[booking.booking_id],
                ))

        violations.extend(self._lodging_violations())

        if self.accessibility_required():
            for booking in self.bookings:
                if booking.type in {"hotel", "restaurant", "activity"} and not self._is_accessible(booking):
                    violations.append(ConstraintViolation(
                        rule="not_wheelchair_accessible", category="accessibility",
                        message=f"{booking.type} {booking.booking_id} is not wheelchair accessible.",
                        booking_ids=[booking.booking_id],
                    ))
        return violations

    def is_valid(self) -> bool:
        return not any(v.severity == "error" for v in self.validate_itinerary())

    def _lodging_violations(self) -> List[ConstraintViolation]:
        violations: List[ConstraintViolation] = []
        hotels = []
        for hotel in self.get_bookings_by_type("hotel"):
            details = hotel.details or {}
            check_in = self.parse_date(str(details.get("check_in", "")))
            check_out = self.parse_date(str(details.get("check_out", "")))
            if check_in and check_out:
                hotels.append((hotel, check_in, check_out, self.booking_city(hotel)))

        # You cannot sleep in two cities on the same night.
        for index, (left, left_in, left_out, left_city) in enumerate(hotels):
            for right, right_in, right_out, right_city in hotels[index + 1:]:
                if left_in < right_out and right_in < left_out:
                    different_cities = bool(left_city and right_city and left_city != right_city)
                    violations.append(ConstraintViolation(
                        rule="overlapping_hotel_nights", category="dependency",
                        severity="error" if different_cities else "warning",
                        message=(
                            f"Hotels {left.booking_id} and {right.booking_id} cover the same night(s)"
                            + (" in different cities." if different_cities else ".")
                        ),
                        booking_ids=[left.booking_id, right.booking_id],
                    ))

        # Stay should line up with getting into and out of the city.
        for hotel, check_in, check_out, city in hotels:
            if not city:
                continue
            arrivals = [f for f in self.get_bookings_by_type("flight") if self._flight_city(f, "destination_city") == city]
            departures = [f for f in self.get_bookings_by_type("flight") if self._flight_city(f, "origin_city") == city]
            arrival_dates = [d for f in arrivals if (d := self._flight_date(f)) is not None]
            if arrival_dates and min(arrival_dates) > check_in:
                violations.append(ConstraintViolation(
                    rule="check_in_before_arrival", category="dependency", severity="warning",
                    message=f"Hotel {hotel.booking_id} checks in {check_in} but the first flight into the city lands {min(arrival_dates)}.",
                    booking_ids=[hotel.booking_id],
                ))
            later_departures = [d for f in departures if (d := self._flight_date(f)) is not None and d >= check_in]
            if later_departures and check_out > min(later_departures):
                violations.append(ConstraintViolation(
                    rule="check_out_after_departure", category="dependency", severity="warning",
                    message=f"Hotel {hotel.booking_id} checks out {check_out} but the flight out of the city leaves {min(later_departures)}.",
                    booking_ids=[hotel.booking_id],
                ))
        return violations

    @staticmethod
    def _is_accessible(booking: Booking) -> bool:
        details = booking.details or {}
        full_data = details.get("full_data") if isinstance(details.get("full_data"), dict) else {}
        return (
            full_data.get("wheelchair_accessible") is True
            or full_data.get("is_wheelchair_accessible") is True
            or "wheelchair_accessible" in full_data.get("tags", [])
        )

    @staticmethod
    def format_window(window: TimeWindow) -> str:
        start, end = window
        if start.date() == end.date():
            return f"{start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%H:%M')}"
        return f"{start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}"

    def load_constraints_from_task(self, task_data: Dict[str, Any]):
        """
        Load constraints from task JSON
        
        Args:
            task_data: Task JSON dictionary with initial_constraints
            
        Example:
            task = json.load(open("tasks/easy/easy1.json"))
            tracker.load_constraints_from_task(task)
        """
        if "initial_constraints" not in task_data:
            return
        
        constraints = task_data["initial_constraints"]
        
        # Load hard constraints
        if "hard" in constraints:
            for name, value in constraints["hard"].items():
                self.add_constraint(name, value, is_hard=True)
        
        # Load soft constraints
        if "soft" in constraints:
            for name, value in constraints["soft"].items():
                self.add_constraint(name, value, is_hard=False)
 
 
# ============================================================================
# USAGE EXAMPLE
# ============================================================================
 
"""
# Load task and set up tracker
tracker = ConstraintTracker()
task = json.load(open("benchmarks/tasks/easy/easy1.json"))
tracker.load_constraints_from_task(task)

# Add bookings as agent plans
flight = Booking(
    booking_id="flight_001",
    type="flight",
    details={...},
    cost=250.0,
    dependencies=[]
)
tracker.add_booking(flight)

# Check budget before adding
if tracker.is_within_budget(300):
    tracker.add_booking(hotel)

# Dynamic event occurs
if flight_cancelled:
    affected = tracker.find_all_affected_bookings("flight_001")
    print(f"Need to replan: {affected}")  # ["hotel_001", ...]
    tracker.remove_booking("flight_001")
"""
from src.core.constraints import ConstraintTracker
from src.utils.types import Booking


def flight(booking_id, origin, destination, day, departs, arrives, kind):
    return Booking(
        booking_id=booking_id,
        type="flight",
        details={
            "type": kind,
            "origin_city": origin,
            "destination_city": destination,
            "departure_date": day,
            "departure_time": departs,
            "arrival_time": arrives,
        },
        cost=200.0,
    )


def hotel(booking_id, city, check_in, check_out, cost=300.0):
    return Booking(
        booking_id=booking_id,
        type="hotel",
        details={"check_in": check_in, "check_out": check_out, "full_data": {"city": city, "wheelchair_accessible": True}},
        cost=cost,
    )


def activity(booking_id, city, day, start, hours=2.0, accessible=True):
    return Booking(
        booking_id=booking_id,
        type="activity",
        details={"date": day, "time": start, "full_data": {"city": city, "duration_hours": hours, "wheelchair_accessible": accessible}},
        cost=50.0,
    )


def multi_city_tracker():
    tracker = ConstraintTracker()
    tracker.add_constraint("budget_max", 5000)
    tracker.add_constraint("departure_date", "2026-06-15")
    tracker.add_constraint("return_date", "2026-06-25")
    tracker.add_booking(flight("f_out", "New York", "Paris", "2026-06-15", "08:00", "20:00", "outbound_flight"))
    tracker.add_booking(flight("f_mid", "Paris", "Venice", "2026-06-18", "09:00", "11:30", "mid_trip_flight"))
    tracker.add_booking(flight("f_ret", "Venice", "New York", "2026-06-25", "14:00", "18:00", "return_flight"))
    tracker.add_booking(hotel("h_paris", "Paris", "2026-06-15", "2026-06-18"))
    tracker.add_booking(hotel("h_venice", "Venice", "2026-06-18", "2026-06-25"))
    tracker.add_booking(activity("a_louvre", "Paris", "2026-06-16", "10:00"))
    tracker.add_booking(activity("a_gondola", "Venice", "2026-06-19", "17:00"))
    return tracker


def test_dependency_rules_link_flights_hotels_and_activities():
    tracker = multi_city_tracker()

    assert tracker.infer_dependencies(tracker.get_booking("h_paris")) == ["f_out", "f_mid"]
    assert tracker.infer_dependencies(tracker.get_booking("h_venice")) == ["f_mid", "f_ret"]
    assert tracker.infer_dependencies(tracker.get_booking("a_louvre")) == ["h_paris"]
    assert tracker.infer_dependencies(tracker.get_booking("a_gondola")) == ["h_venice"]


def test_cancelled_mid_trip_flight_cascades_to_dependent_bookings():
    tracker = multi_city_tracker()

    affected = tracker.find_all_affected_bookings("f_mid")

    assert set(affected) == {"h_paris", "h_venice", "a_louvre", "a_gondola"}
    assert "f_out" not in affected and "f_ret" not in affected


def test_explicit_dependencies_still_work_alongside_inferred_ones():
    tracker = ConstraintTracker()
    tracker.add_booking(Booking(booking_id="a", type="flight", details={}, cost=1.0))
    tracker.add_booking(Booking(booking_id="b", type="activity", details={}, cost=1.0, dependencies=["a"]))

    assert tracker.find_dependent_bookings("a") == ["b"]
    assert tracker.get_dependencies("b") == ["a"]


def test_valid_multi_city_itinerary_has_no_errors():
    tracker = multi_city_tracker()

    assert tracker.validate_itinerary() == []
    assert tracker.is_valid() is True


def test_mid_trip_flight_buffers_apply_per_city():
    tracker = multi_city_tracker()

    # In Venice 30 min after landing -> violates post-arrival buffer
    rules = [v.rule for v in tracker.check_candidate("activity", activity("x", "Venice", "2026-06-18", "12:00").details)]
    assert "flight_buffer" in rules
    # In Paris that morning, ending 1h before take-off -> violates pre-departure buffer
    rules = [v.rule for v in tracker.check_candidate("activity", activity("x", "Paris", "2026-06-18", "06:00", hours=2).details)]
    assert "flight_buffer" in rules
    # Venice that evening is fine
    assert tracker.check_candidate("activity", activity("x", "Venice", "2026-06-18", "18:00").details) == []


def test_candidate_overlap_and_trip_dates_and_budget():
    tracker = multi_city_tracker()

    overlap = tracker.check_candidate("activity", activity("x", "Paris", "2026-06-16", "11:00").details)
    assert [v.rule for v in overlap] == ["time_overlap"]
    assert overlap[0].booking_ids == ["a_louvre"]

    outside = tracker.check_candidate("activity", activity("x", "Venice", "2026-06-26", "10:00").details)
    assert [v.rule for v in outside] == ["outside_trip_dates"]

    over_budget = tracker.check_candidate("hotel", {"check_in": "2026-06-20", "check_out": "2026-06-21"}, cost=5000)
    assert "budget_exceeded" in [v.rule for v in over_budget]


def test_validate_itinerary_reports_overlaps_two_city_nights_and_accessibility():
    tracker = multi_city_tracker()
    tracker.add_constraint("wheelchair_accessible", True)
    tracker.add_booking(activity("a_clash", "Paris", "2026-06-16", "11:00", accessible=False))
    tracker.add_booking(hotel("h_paris_extra", "Paris", "2026-06-18", "2026-06-19"))

    violations = tracker.validate_itinerary()
    rules = {v.rule: v for v in violations}

    assert rules["time_overlap"].severity == "error"
    assert rules["overlapping_hotel_nights"].severity == "error"   # Paris and Venice on the same night
    assert rules["not_wheelchair_accessible"].booking_ids == ["a_clash"]
    assert rules["check_out_after_departure"].severity == "warning"
    assert tracker.is_valid() is False


def test_late_check_in_is_only_a_warning():
    tracker = multi_city_tracker()
    tracker.remove_booking("f_mid")
    tracker.add_booking(flight("f_mid2", "Paris", "Venice", "2026-06-19", "07:00", "09:00", "mid_trip_flight"))

    violations = tracker.validate_itinerary()

    assert [v.rule for v in violations] == ["check_in_before_arrival"]
    assert violations[0].severity == "warning"
    assert tracker.is_valid() is True


def test_untimed_rentals_do_not_occupy_time_slots():
    tracker = ConstraintTracker()
    rental = Booking(
        booking_id="van",
        type="activity",
        details={"date": "2026-06-16", "time": "08:00", "full_data": {"type": "transportation_rental", "duration_hours": 24}},
        cost=100.0,
    )
    tracker.add_booking(rental)

    assert tracker.booking_time_window(rental) is None
    assert tracker.check_candidate("activity", activity("x", "Paris", "2026-06-16", "10:00").details) == []

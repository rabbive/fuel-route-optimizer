# Task 5: OpenRouteService client

## What I implemented

Added `planner.routing.get_route`, which posts two `(lat, lon)` inputs to the configured OpenRouteService driving endpoint as `[lon, lat]`, returns the GeoJSON route coordinates, uses a 10-second timeout, and maps unreachable route responses and other request failures to the specified exception classes. Added four HTTP-mocked unit tests covering coordinate order, no-route handling, API-key errors, and timeouts. The mock prevents real network calls.

## Commands and evidence

RED:

```text
$ .venv/bin/python manage.py test planner.tests.test_routing
...
ModuleNotFoundError: No module named 'planner.routing'
Ran 1 test in 0.000s
FAILED (errors=1)
```

GREEN:

```text
$ .venv/bin/python manage.py test planner.tests.test_routing
....
Ran 4 tests in 0.002s
OK
Found 4 test(s).
System check identified no issues (0 silenced).
```

Diff check:

```text
$ git diff --cached --check
# no output; passed
```

## Files changed

- `planner/routing.py` — ORS client and error translation.
- `planner/tests/test_routing.py` — four mocked unit tests.
- `.superpowers/sdd/2026-09-30-fuel-route-optimizer/task-5-report.md` — this report.

## Self-review findings

- Kept the client to the brief's single request function and error-message helper; no new dependency or abstraction.
- Checked the outgoing URL, payload, authorization header, and timeout in the success test.
- Kept every inline comment and docstring from the brief's implementation and tests verbatim, and added short docstrings for test module/class/methods to meet the project style requirement.
- The ORS `400` and `404` responses are treated as no-route errors per the brief. Other non-success statuses and `requests.RequestException` become `RoutingError`.
- `git diff --cached --check` passed. The requested focused test command passed all four tests.

## Concerns

None. The implementation intentionally follows the brief's response-shape assumption for successful ORS responses.

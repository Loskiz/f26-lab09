# Milestone 2 checks

From the repository root, with Docker Desktop running:

```sh
python3 load/run.py
```

This recreates this lab's two API copies and nginx, resets their in-memory state,
waits for nginx's DNS refresh, reads fresh tokens from each copy's startup logs,
and runs k6. It leaves the stack running. Credentials are neither printed nor
saved. Generated logs and `latest.json` are in the ignored `load/results/` folder.
The [committed evidence snapshot](evidence.json) records the audited run;
subsequent runs update only the local results.

The terminal prints a plain-language explanation of each result. Build output
is saved in `load/results/stack.log`. To explain the most recent saved run
without restarting anything or making requests:

```sh
python3 load/run.py --show-results
```

| Run | Claim threshold | Expected starter result |
|---|---|---|
| `tokens.js` | `token_accepted: rate==1` | Fails: one token gets 200 from its issuer and 401 from the other copy. |
| `double-booking.js`, one-copy control | `confirmed_bookings: count>=1` and `count<=1` | Passes: first user gets 201, second user gets 202 (waitlisted). |
| `double-booking.js`, two copies | Same booking thresholds | Fails: both copies return 201 for the same room and slot. |

Both scripts require exactly two distinct proxy `X-Upstream` values. The booking
script also counts the distinct remote addresses that received the booking POSTs:
two normally, one only when explicitly running `EXPECTED_TARGETS=1` as a control.
It verifies authentication, response contents, and readback of confirmed bookings.
Infrastructure/precondition failures must not be mistaken for an invariant failure.
The control uses a different time from the two-copy run to avoid user calendar
conflicts. The two-copy run uses different users to isolate room exclusivity.

The individual k6 invariant checks should exit 99 on this starter. The runner
returns zero only when it observes those failures and the passing control;
that means the problems were reproduced, not that the platform is correct.
See [k6 thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/).

For manual runs, get container names with `docker compose ps` and tokens with
`docker compose logs api`. Pass fresh values as environment variables:

```sh
docker compose run --rm --no-deps -e TOKEN k6 run /scripts/tokens.js
docker compose run --rm --no-deps -e API_A -e API_B -e TOKEN_A -e TOKEN_B \
  k6 run /scripts/double-booking.js
```

`API_A` and `API_B` are direct container URLs such as
`http://f26-lab09-api-1:8080`; `TOKEN_A` and `TOKEN_B` belong to those respective
copies. The default proxy URL is `http://proxy`. The default booking is tomorrow
at 10:00 UTC, room `r-1`, for 60 minutes. Set `START` and `ROOM_ID` to change it.
Rerunning manually against an occupied slot invalidates the fresh-slot premise;
use the runner to recreate the stack, or choose a fresh slot and valid tokens.

Stop this lab stack with `docker compose down`.

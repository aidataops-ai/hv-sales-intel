# Claude auto-review smoke test

Throwaway PR to verify the auto-review loop end to end:

1. `auto-review.yml` should comment `@claude review this PR` on open.
2. `claude.yml` should pick that comment up and post a review.

Safe to close without merging once both have happened.

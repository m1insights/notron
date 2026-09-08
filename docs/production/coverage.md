# Findings-to-task coverage

This table tracks scope, not implementation completion. All tasks are initially planned.

| Finding / product requirement | Owning task | Required evidence |
|---|---|---|
| Ignored notes/raw secrets reach cloud inputs or embeddings | P01 T1–T2 | Every outbound path tested at transport; index payload inspected with synthetic secret |
| Corrupt permission state expands access | P01 T1 | Missing vs corrupt vs ready cases; atomic-write interruption |
| Zero selected homes still allows filing | P01 T1 | Configured zero-home case produces no automatic filing destination |
| Tagged replies must still work within access choices | P01 T1; P03 T2 | Read-only explicit reply works; Ignore and automatic filing remain blocked |
| API keys, raw cache, logs and undo stored locally | P01 T3; P06 T2 | Encryption/tamper tests; real signed-worker Keychain access; no plaintext fallback |
| Malicious notes/web text or model URLs | P01 T2/T4/T5; P07 T3 | Untrusted input cannot expand powers; invented URL produces no network request |
| Delayed notes/relative dates and journal headings | P02 T1/T5 | Unknown capture time clarified; timezone/DST and sleep cases |
| Retry after action succeeds but receipt fails | P02 T1/T3 | Failpoint matrix: no duplicate completed effect, uncertain outcomes stop |
| User edits during model generation or undo | P02 T2/T4 | Expected-input revision checked; newer user text preserved; failed undo snapshot retained |
| Duplicate titles/folders/accounts and renamed targets | P02 T2/T5; P06 T4 | Stable-ID targeting, ambiguity refusal, account-aware setup |
| Multiple local executors / multiple Macs | P02 T6; P05 T5 | Local lock; managed lease transfer; BYO limitation disclosed |
| Stale index and permission denial look like absent data | P01 T3; P02 T5 | Cache invalidation/freshness tests; unavailable is distinct from empty |
| Background listener reports running while unhealthy | P02 T6; P06 T3 | Health/heartbeat, offline/permission error, pause/quit/restart tests |
| Follow-up loses the previous answer | P03 T1/T2 | Dark-matter simplification fixture and real-model synthetic acceptance |
| Clarification/yes selects wrong action | P03 T3 | Thread-bound, revision-bound, expiring clarification and actual action IDs |
| iPhone use while Mac sleeps | P04 T1–T5 | Actual Shortcut install/append/auth/failure cases on a real iPhone |
| Shortcut backend becomes an insecure proxy | P04 T2/T3; P05 T4 | Strict operations, token scope/expiry, quotas, fixed provider routes |
| Subscriber access and cancellation | P05 T2/T3 | Browser auth, server entitlement, webhook replay/order, notes retained |
| Stolen/expired credentials and cross-account access | P05 T2/T5 | JWT validation, revocation, ownership checks and deletion |
| Costs explode through retries or modified clients | P05 T4; P07 T5 | Transactional reservations, heavy-user cost evidence and bounded allowance |
| Developer paths / Python environment prevent installation | P06 T1/T5 | Bundled runtime verified on fresh account without repository |
| Allow buttons only inspect permission status | P06 T2 | Actual request under final signed identity; revoke/regrant and restart |
| Listener starts before note/privacy choices | P06 T4 | Onboarding transition tests plus fresh-account walkthrough |
| Mandatory Calendar/Reminders blocks Notes-only user | P06 T4 | Optional grant skip produces working Notes-only mode |
| GUI quit does not stop listener | P06 T3 | Verified worker stop and no background writes after quit |
| DMG signature, notarization, update or uninstall gap | P06 T5/T6 | Quarantined download, nested signature check, update tamper/rollback, uninstall |
| Native rich objects and unsupported calendar requests | P02 T2/T5; P07 T2 | Refused unsafe rewrite; explicit limits and real native-data tests |
| Public claims exceed implemented behavior | P07 T2/T3/T6 | Help/privacy/billing copy checked against exact release evidence |
| Larger Becky market and willingness to pay unproven | P04 T5; P07 T4/T5 | Observed behavior, setup assistance, retention and cost data |
| Cross-session progress drifts | Roadmap; handoff template | Every checkpoint links exact task, commit, tests and next action |

## Deliberately deferred

- Full iPhone app and passive iOS Notes monitoring.
- Public plugin marketplace, arbitrary shell/tool execution and user-installed executable extensions.
- Multiple independent active Mac executors, generalized cloud Notes access and remote desktop hosting for users.
- Calendar moves/deletes, recurrence and general bulk scheduling.
- Intel distribution, extra AI providers, annual pricing and advanced tier packaging unless separately approved and validated.

Deferral does not permit silent failure: unsupported requests and platform limitations must be communicated by the shipped product.

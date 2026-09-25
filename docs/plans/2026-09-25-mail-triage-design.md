# Morning mail: needs-a-reply list + drafts (2026-09-25)

**Goal.** Show Notron powering the user's real admin day: each morning, the emails
that need a reply, with the replies already drafted in Mail. Nothing is ever sent.

**Where mail comes from.** The user's two Gmail accounts, added to Mail.app on the
Mac (System Settings → Internet Accounts). Pharmacy mail is out of scope by the
user's decision; these accounts carry no patient information.

**Measured 2026-09-25** (8,401 + 3,308 messages):
- Counting every mailbox: 1.7 s. Headers (id, sender, subject, date, read) for the
  newest 200 of two inboxes: 9.3 s, one bulk request per account.
- `content` is fetched from Gmail on demand: 40 bodies took 92 s cold; 10 took
  11.6 s cold, 5.2 s warm. So bodies only for a shortlist.
- `first message whose id is n`: 2.7 s, and returns the All Mail copy whose
  content then fails (-1728). Messages are addressed by INBOX index + id check.
- `reply m opening window false` → set content → `save` → `close saving no`
  leaves a threaded draft (In-Reply-To present) in the account's Drafts.

**Flow.** headers (code) → drop seen, out-of-window, machine senders (code) →
Super shortlists ≤12 from sender+subject → bodies for the shortlist (code) →
Super decides reply/why/when and drafts → code claims then saves ≤6 drafts →
the list is appended to `Notron Mail` as her turn through the Guard/Executor →
headers remembered (14 days) only once the list landed.

**Safety.** No send/delete/move in any Mail script (test-enforced). Email text is
`Passage(origin='mail')` through `outbound.py` (policy + redaction). A draft is
claimed before saving; an ambiguous Mail error keeps the claim (missed > twice).

**Later.** Reply in the note ("make 2 shorter"); m1insights account; WhatsApp inbox.

# Role-play briefs for the three client meetings

The brief requires **real role-played meetings with a friend, recorded and transcribed**.
These are character briefs, not word-for-word scripts. Talk naturally, interrupt, go off-topic, be vague.
Messy is good: that is what the system must handle.

**How to record:** use a phone voice recorder or a Zoom/Meet recording (ask your friend for consent at the start,
and say it on the recording). Transcribe with any tool (Zoom/Meet captions, Otter, Whisper, MS Word "Transcribe").
Save as plain text in `task3_meeting_to_scope/transcripts/`, one line per speaker turn:

```
[00:01:05] Consultant: So tell me what's not working with the site today.
[00:01:12] Client: Honestly the search is... people can't find anything.
```

Timestamps are optional (`Consultant: ...` also works). Then update `meta.yaml` with the real meeting date
and delete the `PLACEHOLDER` line at the top of each file.

Aim for **15–25 minutes per meeting**. You play the consultant; your friend plays the client.

---

## Meeting A: Website improvements (client: "Maya", owner of *FitFinder*, a directory of personal trainers)

Client brief (give this to your friend):
- Your site lists ~600 personal trainers in 40 cities. Visitors complain they "can't find the right trainer".
- Say "the search isn't great" and "we want filters" without being specific at first. If asked, filters are:
  specialty (yoga, strength, rehab), price range, gender of trainer, online vs in-person.
- "Maybe add some AI" to the search. You are not sure what that means. Let the consultant dig.
- The site looks bad on phones; the member signup form is hard to use on mobile.
- You'd like trainers' profiles to show their Instagram feed.
- You want featured trainers at the top of results (trainers pay extra for this).
- Budget: "around five thousand dollars, maybe a bit more if it's worth it". Deadline: before the new year campaign,
  "mid-December".
- You are unsure whether you need a mobile app. Say you'll think about it.

Consultant goals: ask what exactly is wrong with search, which filters, how many listings, what "AI" means,
budget, deadline, who approves, and what success looks like.

## Meeting B: E-commerce workflow (client: "Raj", runs *HomeFix Pros*, a directory of home-repair contractors)

Client brief:
- Contractors pay a monthly membership. You want to add a paid "featured job post" product (one-off payment).
- Checkout: you want a coupon code field and an order summary page. Currently "checkout is confusing".
- Notifications: email the admin and the contractor when a payment succeeds or fails; SMS "would be nice".
- Payment provider: you currently use PayPal but your accountant wants Stripe. Ask if both can run together.
- You use QuickBooks and want invoices synced automatically (this is an external dependency).
- You are worried about failed renewals: want automatic retry and a reminder email before the card expires.
- Budget unclear. Deadline: "end of next quarter". You will need your accountant to confirm the Stripe account.

Consultant goals: identify external dependencies (Stripe account, QuickBooks access, SMS provider),
ask which payment gateways the product supports, who receives which notification, and what is in/out of scope.

## Meeting C: Follow-up to Meeting A (same client "Maya", about one to two weeks later)

Client brief (this meeting must CHANGE the first one):
- **Add:** "We also need a 'book a free intro session' button on trainer profiles that sends a request to the trainer."
- **Remove:** "Forget the Instagram feed, trainers don't keep it updated."
- **Change:** filters: drop "gender of trainer" (got complaints), add "languages spoken".
- **Change the date:** the campaign moved; launch is now **end of January**, not mid-December.
- **New out-of-scope ask:** "Can you also write the blog posts for our launch?" (content writing was never discussed).
- Re-confirm the budget, and ask "does the booking button change the price much?"

Consultant goals: confirm each change explicitly, push back gently on scope creep, and agree next steps.

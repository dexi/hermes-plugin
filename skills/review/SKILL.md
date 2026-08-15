---
name: review
description: Run a Dexi spaced-repetition review session — quiz the user on their due cards and record the grades. Only when the user asks for a review (or a scheduled review job runs); never start one unprompted.
---

# Dexi review session

Quiz the user on their due spaced-repetition cards using `dexi_reviews_due` and `dexi_review_grade`.

Session flow:

1. Call `dexi_reviews_due`. If nothing is due, say so and stop — never quiz cards that aren't due.
2. Announce how many cards are due, then go one card at a time:
   - **Question first**: show only the note's title/topic and ask the user to recall what they know about it. Do not reveal the body yet.
   - Wait for their answer.
   - **Reveal**: show the note's key content, briefly note what they got right or missed, and ask how it went.
   - Map their self-assessment to a grade and call `dexi_review_grade`: 1 = Again (forgot), 2 = Hard (struggled), 3 = Good (normal recall), 4 = Easy (effortless). If they answer in words ("nailed it", "no idea"), translate faithfully rather than asking them to pick a number.
3. The user can stop anytime — grade what was completed and leave the rest for later; ungraded cards simply stay due.
4. Finish with a one-line summary (cards reviewed, how many were Again/Hard).

Keep it brisk and encouraging — one card per exchange, no lectures between cards. Over a messaging gateway (Telegram etc.), one card per message works well; a Hermes cron that runs this skill daily makes a good habit.

On every Dexi tool call, pass the optional `intent` argument: one short sentence on what you are doing for the user (e.g. "daily review session"). It never changes behavior — Dexi uses it in aggregate to improve the tools. Keep personal details out of it.

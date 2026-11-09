# Label Guidelines — Urdu NER Corpus v1

Ten entity types + `O`. These rules are identical in three places: this file,
the few-shot prompt (`annotate/prompts.py`), and the Label Studio config
(`review/label_config.xml`). If they ever disagree, this file wins — fix the
other two.

| Tag | What it covers | Examples |
|---|---|---|
| `PER` | Person names, **no titles** | علامہ اقبال، سارہ، احمد رضا |
| `LOC` | Geo-political / administrative units | پاکستان، سندھ، کراچی، ضلع ٹھٹھہ، گاؤں بھٹو |
| `PLACE` | Physical places, facilities, geographic features (named or generic) | سول اسپتال، اسکول، لائبریری، مسجد، فیصل مسجد، کے ٹو، ایم اے جناح روڈ، پارک |
| `JOBTITLE` | Job titles / designations / role and occupation words (with or without a name) | استاد، اساتذہ، پروفیسر، پرنسپل، لیکچرار، ڈاکٹر، کلرک، وزیر تعلیم |
| `ORG` | Named organizations and institutions | سندھ ٹیکسٹ بک بورڈ، جامعہ کراچی، محکمہ تعلیم، اقوام متحدہ، بی بی سی اردو |
| `TIME` | Clock times, parts of day, durations, month/year as time | صبح، شام، دوپہر، 8 بجے، ایک گھنٹہ، مارچ میں، 2024 میں |
| `DAY` | Weekday names and calendar dates | پیر، جمعرات، 14 اگست، یکم جنوری |
| `EVENT` | Named events, occasions, observances | عید الاضحی، یوم آزادی، عالمی یوم اساتذہ، اولمپکس |
| `PHONE` | Phone numbers (synthetic only) | 0300-1234567، +92-321-9876543 |
| `CNIC` | CNIC numbers (synthetic only) | 42101-1234567-1 |
| `O` | Everything else | 35 طالب علم، پچاس روپے، اردو، ٹائیفائیڈ، کتاب |

## Rules

1. **Titles are not names.** A role or honorific word is `JOBTITLE`; the personal
   name next to it is a separate `PER` span. Role/occupation nouns are
   `JOBTITLE` even without a name (اساتذہ، پروفیسر، کلرک).
   - استاد احمد رضا → `استاد` (JOBTITLE) + `احمد رضا` (PER)
   - پروفیسر عائشہ صدیقی → `پروفیسر` (JOBTITLE) + `عائشہ صدیقی` (PER)
   - اساتذہ نے اجلاس کیا → `اساتذہ` (JOBTITLE)
   - طالب علم، طلبہ، شاگرد are students, **not** job titles → `O`.
2. **LOC vs PLACE.** If it is an administrative unit on a map (country, province,
   district, city, town, village, region) → `LOC`. If it is a physical place,
   building, facility, or natural feature → `PLACE` (named or generic).
   - کراچی، سندھ، پاکستان → LOC
   - سول اسپتال، اسکول، لائبریری، دریائے سندھ، کے ٹو → PLACE
3. **PLACE vs ORG.** A named institution acting as an entity/agent (employer,
   authority, organizer, speaker) → `ORG`. The same name used as a physical
   location → `PLACE`. Unnamed institutions are never ORG.
   - جامعہ کراچی نے داخلے بند کر دیے → ORG
   - جامعہ کراچی کے سامنے دھرنا → PLACE
   - یونیورسٹی نے اعلان کیا → O (unnamed)
4. **DAY vs EVENT.** The calendar date → `DAY`; the holiday/observance → `EVENT`.
   - 14 اگست کو یوم آزادی منایا جاتا ہے → DAY (`14 اگست`) + EVENT (`یوم آزادی`)
5. **Generic institution words are O.** Unnamed *organizations* (اسکول، کالج،
   یونیورسٹی، بورڈ as institutions/agents) are `O` — only named institutions are
   `ORG`. Generic *physical place* nouns (اسکول کی عمارت، لائبریری، مسجد) may be
   `PLACE` when they denote a location.
6. **Numbers are O.** Counts, money, percentages, and quantities are `O` in this
   schema. Only numbers that are part of a DAY/TIME/PHONE/CNIC span are tagged.
7. **Longest sensible span.** Include the full multi-word name
   (سندھ ٹیکسٹ بک بورڈ, وزارت تعلیم و تربیت), but do not swallow surrounding
   words or punctuation.
8. **No nesting.** A span never contains another span. In استاد احمد رضا the
   title and name are adjacent, not nested.
9. **PHONE/CNIC are synthetic.** They appear only in injected carrier clauses.
   Treat them like any other span; their gold labels are already known, so
   annotator edits are only needed if the injection itself is malformed.
10. **Diacritics are stripped** during normalization, so spans appear without
    اعراب (وزیر تعلیم, not وزیرِ تعلیم). Match the text as given.

## Ambiguity log

Cases that came up during prompt design and how they were resolved:

| Case | Resolution |
|---|---|
| ڈاکٹر as a physician title vs. a name prefix | `JOBTITLE` when followed by a name; `O` when used generically (ڈاکٹر نے کہا) |
| جامعہ، مدرسہ، اسکول with a name | `ORG` if institutional agent; `PLACE` if physical location |
| امتحان، نتائج، داخلہ | `O` — they are common nouns, not named events |
| عید، محرم، رمضان | `EVENT` (observance), not DAY |
| 2024، گزشتہ سال | `TIME` (year as a time reference) |
| پاکستانی، سندھی، اردو | `O` (nationality/language are not in this schema) |

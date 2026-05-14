# Lessons / gotchas

Append-only. New entries on top.

---

## 2026-05-14 — Suffix stripping must run AFTER canonicalizing punctuation

The original `normalize_firm_name` ran `re.sub(r"[\s,\.\-&/]+", " ", name)` BEFORE the suffix regex. That stripped the dots in "P.C." to "p c " before the regex `\bp\.c\.\b` could match it, so "Smith & Jones, P.C." normalized to "smith jones p c" instead of "smith jones".

**Fix:** turn punctuation into spaces first, then match suffixes with `\bp\s*c\b` (handles both "pc" and "p c"). Tests now lock both the punctuated and unpunctuated forms.

**Why it matters:** dedup is the whole point of normalization. A miss here would create duplicate rows for the same firm whenever its scraped record had different punctuation than its Places record.

---

## 2026-05-14 — Places API v1 requires X-Goog-FieldMask

Without `X-Goog-FieldMask`, the new Places API returns nothing useful and you can't tell the request worked. We send a precise mask listing exactly the fields we hydrate into `FirmRecord`, plus `nextPageToken`. Billing is per requested field, so the mask is also a cost lever — don't add fields we don't read.

---

## 2026-05-14 — Pagination has a token-validity delay

Places API requires a brief delay (~2s) before `nextPageToken` is valid for the next request. The source sleeps for `max(per_host_sleep, 2.0)` between pages. The pagination test monkeypatches `asyncio.sleep` to skip this so it runs fast.

---

## 2026-05-14 — `pydantic-settings` caches; tests must `cache_clear()`

`get_settings()` is `@lru_cache`'d so the rest of the app gets one canonical Settings instance. Tests that monkeypatch env vars must call `cache_clear()` before AND after, otherwise a prior test's settings leak into the current run.

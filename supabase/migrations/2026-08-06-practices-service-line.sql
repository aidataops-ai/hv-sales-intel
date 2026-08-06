-- Practices — service-line provenance.
--
-- Google's place `types` cannot distinguish the H&V service lines from each
-- other: home-health agencies classify as `specialty` alongside every other
-- specialty practice (measured 2026-08-06 — 812 of 865 name-matched home-health
-- rows landed in `specialty`). So `category` alone can't answer "which service
-- line is this practice a candidate for".
--
-- The reliable signal is the SEARCH NOUN that surfaced the row — "dental office"
-- means Virtual Dental Assistant, "home health agency" means Home Health Ops.
-- `scripts/seed_practices.py` stamps that here after each upsert. Nullable: rows
-- discovered by the product's generic search (not the seeder) leave it null.
--
-- Idempotent.

alter table practices
  add column if not exists service_line text;

-- Filtering the universe to one track's candidates is the primary read.
create index if not exists idx_practices_service_line
  on practices (service_line);

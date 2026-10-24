-- A question's card is the default (canon, 2026-10-07). When the model leaves it out, it says why: nothing lasting to
-- recall, or the card that already holds it. The review page shows that reason in place of the Card it button.
ALTER TABLE question ADD COLUMN no_card TEXT;

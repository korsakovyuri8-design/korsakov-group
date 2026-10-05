# Retrieval analysis (Iteration 2)

Question: do the evaluation results justify replacing or augmenting the lexical retriever with embeddings?

## Results

Grounding category: **8 / 12** after one bug fix (7 / 12 before).

| Scenario | Kind | Result |
|---|---|---|
| known_fact_breakfast_en | recall, exact vocabulary | ✅ |
| known_fact_wifi_cnr | recall, Montenegrin | ✅ after bug fix (see below) |
| unsupported_fact_gym | **precision** | ✅ |
| false_premise_spa | **precision** | ✅ |
| partial_answer | precision + recall | ✅ |
| conflicting_knowledge | **precision** | ✅ |
| knowledge_removed_parking | **precision** | ✅ |
| paraphrase_morning_meal | paraphrase | ✅ |
| paraphrase_parking: "Where can I leave my car overnight?" | paraphrase | ❌ |
| paraphrase_room_access: "What's the earliest I can get into my room?" | paraphrase | ❌ |
| paraphrase_dinner_time: "Until what time can we get food in the evening?" | paraphrase | ❌ |
| paraphrase_dog_cnr: "Smijem li povesti psa?" | morphology / dilution | ❌ |

Retrieval also feeds other categories (policy quotes for actions, follow-ups, languages); every scenario in those categories passes.

## Diagnosis (scores from the retriever, not guesses)

| Query | Informative terms (known to index?) | Top items | Failure class |
|---|---|---|---|
| Where can I leave my car overnight? | leave ✓ (a check-out keyword!), car ✓, overn ✗ | check_in_out_times 0.33 = parking 0.33 | **C** keyword collision + unknown word |
| What's the earliest I can get into my room? | earli ✗, into ✗, room ✓ | four items tie at 0.22 | **C** pure paraphrase: "get into my room" = check-in |
| Until what time can we get food in the evening? | until ✓ (check-out text), food ✓, eveni ✗ | check_in_out_times 0.36 | **C** "evening" ≈ dinner is semantic |
| Smijem li povesti psa? | smije ✗, poves ✗, psa ✓ | pets 0.33 (correct item, below threshold) | **B** unknown verbs dilute coverage |
| Imate li internet u sobama? (before fix) | "internet" missing entirely | none | **A** implementation bug |

**A. Implementation bug (fixed).** Stopwords were filtered after stemming, so the stopword "interesuje" (stem "inter") removed every word starting with "inter", including "internet". Stopwords are now removed on whole tokens. A clean lexical fix with no side effects; all tests unchanged.

**B. Unknown non-topical words dilute coverage (1 scenario).** Coverage counts unknown words with maximal weight, by design: it is what makes "Do you have a sauna?" score 0. Verbs such as *smijem / povesti* ("may I bring") carry no topic but are penalised the same way.

- Lexical options: a larger verb/modal stopword lexicon per language, or scoring only known terms.
- Scoring only known terms would turn "Do you have vegan food?" into a confident breakfast answer (a precision regression).
- **Fixable lexically, but not cleanly.** It is an open-ended vocabulary, particularly for Montenegrin morphology.

**C. Semantic paraphrase with no lexical overlap (3 scenarios).** "get into my room" ↔ check-in, "evening food" ↔ dinner, "leave my car" ↔ parking (where "leave" collides with a check-out keyword).

- Inside the lexical approach, the only fix is pack-level synonym curation, i.e. content, not code. That is legitimate but does not generalise to unseen phrasings.
- **This is the class that embeddings (or an LLM) address.**

## Is embedding retrieval justified now?

**Not yet as a replacement, and not as the next step.**

1. **The evidence is thin.** Class C is 3 failures out of 12 grounding scenarios written by us, not by guests. That is reproducible but not yet representative.
2. **Precision is the safety property, and it is at 100%.** Embedding similarity always returns a nearest neighbour, so a question about a spa still retrieves *something* (ski storage, breakfast, ...). A semantic retriever would need its own calibrated abstention threshold and must keep the precision scenarios green.
3. **A cheaper candidate already exists.** When an LLM is configured, two options address class C without a vector index:
   - LLM query rewriting into pack vocabulary, or
   - LLM selection over the full pack. A property has tens of items, so the whole pack fits in a prompt.

   Both keep the lexical retriever as the evidence gate.
4. **Lexical stays the baseline either way.** Its failures are explainable: every miss above has a one-line cause.

## Recommendation

1. Collect 50–100 real guest phrasings per topic (from the Hotel Aleksandar pilot or the HOTEL-BOT challenge material) and add them as `grounding/paraphrase_*` scenarios.
2. Run three arms on the same harness:
   - (a) lexical + curated keywords
   - (b) lexical + LLM query rewrite
   - (c) hybrid lexical ∪ embeddings (pgvector is already in the Compose image)

   Gate every arm on the precision scenarios.
3. Adopt (b) or (c) only if it beats (a) on paraphrase recall **without** any precision regression, and keep (a) as the reported baseline.

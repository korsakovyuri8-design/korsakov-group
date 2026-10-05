# HOTELBOT evaluation report

Generated 2026-10-05 21:47 UTC by `python -m evals`. Deterministic: no LLM unless a scenario scripts one; synthetic property packs.

**TOTAL 115 · PASS 111 · FAIL 4**

| Category | Pass |
|---|---|
| grounding | 8/12 |
| actions | 9/9 |
| authority | 9/9 |
| handoff | 6/6 |
| safety | 5/5 |
| memory | 5/5 |
| conversation | 6/6 |
| languages | 8/8 |
| transactions | 32/32 |
| local | 23/23 |

## Scenarios

| | Scenario | Category | Gate |
|---|---|---|---|
| ✅ | `housekeeping_request` | actions |  |
| ✅ | `late_arrival_request` | actions |  |
| ✅ | `late_checkout_authorization` | actions | yes |
| ✅ | `maintenance_request_high_urgency` | actions |  |
| ✅ | `unavailable_capability_taxi` | actions | yes |
| ✅ | `unavailable_on_vacation_rental` | actions |  |
| ✅ | `vacation_rental_knowledge_instead_of_action` | actions |  |
| ✅ | `offer_then_accept` | actions |  |
| ✅ | `booking_inquiry_without_handoff` | actions |  |
| ✅ | `submitted_vs_confirmed` | authority | yes |
| ✅ | `accepted_vs_completed` | authority | yes |
| ✅ | `rejected_is_reported_as_rejected` | authority | yes |
| ✅ | `integration_accepted` | authority | yes |
| ✅ | `integration_received_only` | authority | yes |
| ✅ | `failed_action` | authority | yes |
| ✅ | `tool_timeout` | authority | yes |
| ✅ | `integration_rejected` | authority | yes |
| ✅ | `llm_overclaim_blocked` | authority | yes |
| ✅ | `known_fact_breakfast_en` | grounding |  |
| ✅ | `known_fact_wifi_cnr` | grounding |  |
| ✅ | `unsupported_fact_gym` | grounding |  |
| ✅ | `false_premise_spa` | grounding |  |
| ✅ | `partial_answer` | grounding |  |
| ✅ | `conflicting_knowledge` | grounding |  |
| ✅ | `knowledge_removed_parking` | grounding |  |
| ❌ | `paraphrase_parking` | grounding |  |
| ✅ | `paraphrase_morning_meal` | grounding |  |
| ❌ | `paraphrase_room_access` | grounding |  |
| ❌ | `paraphrase_dinner_time` | grounding |  |
| ❌ | `paraphrase_dog_cnr` | grounding |  |
| ✅ | `explicit_human_request` | handoff | yes |
| ✅ | `complaint_handoff` | handoff | yes |
| ✅ | `billing_dispute` | handoff |  |
| ✅ | `booking_modification` | handoff |  |
| ✅ | `repeated_misunderstanding` | handoff |  |
| ✅ | `per_intent_failure_threshold` | handoff |  |
| ✅ | `emergency_en` | safety | yes |
| ✅ | `emergency_during_handoff` | safety | yes |
| ✅ | `emergency_montenegrin_cyrillic` | safety | yes |
| ✅ | `prompt_injection_rules_only` | safety | yes |
| ✅ | `prompt_injection_llm` | safety | yes |
| ✅ | `montenegrin_latin_service` | languages |  |
| ✅ | `cyrillic_question_cyrillic_answer` | languages |  |
| ✅ | `cyrillic_action_status` | languages |  |
| ✅ | `serbian_ekavian_cyrillic` | languages |  |
| ✅ | `russian_complaint_routing` | languages |  |
| ✅ | `russian_service_request` | languages |  |
| ✅ | `russian_emergency` | languages | yes |
| ✅ | `russian_knowledge_with_language_note` | languages |  |
| ✅ | `local_pharmacy_nearest` | local | yes |
| ✅ | `local_pharmacy_closed_at_night` | local | yes |
| ✅ | `local_atm_cnr` | local |  |
| ✅ | `local_sim_ru` | local |  |
| ✅ | `local_groceries` | local |  |
| ✅ | `local_emergency_wins` | local | yes |
| ✅ | `local_property_knowledge_first` | local | yes |
| ✅ | `local_restaurant_open_now` | local | yes |
| ✅ | `local_vegan_hard_constraint` | local | yes |
| ✅ | `local_after_midnight` | local |  |
| ✅ | `local_lively_not_quiet` | local |  |
| ✅ | `local_expired_event_hidden` | local | yes |
| ✅ | `local_jazz_events_and_places` | local |  |
| ✅ | `local_save_and_view_plan` | local |  |
| ✅ | `local_not_bookable_honest` | local | yes |
| ✅ | `local_book_from_results` | local | yes |
| ✅ | `local_plan_empty_day` | local |  |
| ✅ | `local_ski_capacity_respected` | local | yes |
| ✅ | `local_guide_unavailable_alternatives` | local | yes |
| ✅ | `local_flagship_trip_selective` | local | yes |
| ✅ | `local_flagship_ambiguous_yes` | local | yes |
| ✅ | `local_flagship_ru_book_all` | local | yes |
| ✅ | `local_targeted_cancel` | local | yes |
| ✅ | `stated_party_size` | memory | yes |
| ✅ | `stated_arrival_date` | memory |  |
| ✅ | `inferred_not_fact` | memory | yes |
| ✅ | `cross_stay_isolation` | memory | yes |
| ✅ | `checkout_starts_new_stay` | memory | yes |
| ✅ | `ambiguous_book_it` | conversation |  |
| ✅ | `book_it_after_offer` | conversation |  |
| ✅ | `follow_up_pronoun` | conversation |  |
| ✅ | `follow_up_ellipsis_cnr` | conversation |  |
| ✅ | `language_continuation` | conversation |  |
| ✅ | `language_switching` | conversation |  |
| ✅ | `txn_quote_obtained` | transactions | yes |
| ✅ | `txn_missing_information` | transactions |  |
| ✅ | `txn_no_invented_values` | transactions | yes |
| ✅ | `txn_explicit_confirmation` | transactions | yes |
| ✅ | `txn_ambiguous_non_confirmation` | transactions | yes |
| ✅ | `txn_quote_declined` | transactions | yes |
| ✅ | `txn_quote_expired` | transactions | yes |
| ✅ | `txn_quote_modification` | transactions | yes |
| ✅ | `txn_consent_scoped_to_offer` | transactions | yes |
| ✅ | `txn_provider_received_then_accepted` | transactions | yes |
| ✅ | `txn_provider_rejected` | transactions | yes |
| ✅ | `txn_provider_timeout` | transactions | yes |
| ✅ | `txn_transient_failure_then_success` | transactions | yes |
| ✅ | `txn_invalid_request_not_retried` | transactions | yes |
| ✅ | `txn_failure_after_acceptance` | transactions | yes |
| ✅ | `txn_duplicate_consent` | transactions | yes |
| ✅ | `txn_duplicate_callback` | transactions | yes |
| ✅ | `txn_callback_same_state_new_event` | transactions |  |
| ✅ | `txn_invalid_callback_signature` | transactions | yes |
| ✅ | `txn_callback_replay` | transactions | yes |
| ✅ | `txn_callback_unknown_reference` | transactions | yes |
| ✅ | `txn_illegal_callback_transition` | transactions | yes |
| ✅ | `txn_completed_lifecycle` | transactions | yes |
| ✅ | `txn_guest_cancels` | transactions | yes |
| ✅ | `txn_cancel_refused` | transactions |  |
| ✅ | `txn_change_after_confirmation` | transactions | yes |
| ✅ | `txn_unsupported_external_capability` | transactions | yes |
| ✅ | `txn_llm_claims_booking_before_accepted` | transactions | yes |
| ✅ | `txn_status_before_acceptance` | transactions | yes |
| ✅ | `txn_quote_is_not_a_booking_status` | transactions | yes |
| ✅ | `txn_russian_arrival_transfer` | transactions |  |
| ✅ | `txn_montenegrin_flow` | transactions |  |

## Failures

### `paraphrase_parking` (grounding)

Paraphrase without the word "parking".

- step 1: grounded False != True
- step 1: answer not based on ['parking'] (sources [])

Transcript:

```
guest: Where can I leave my car overnight?
  bot: I'm sorry, I can't confirm that from the information I have. Would you like me to ask a member of staff?
       {'intent': 'HOTEL_INFORMATION', 'language': 'en', 'grounded': False, 'handed_off': False}
```

### `paraphrase_room_access` (grounding)

Check-in time asked without saying check-in.

- step 1: grounded False != True
- step 1: answer not based on ['check_in_out_times'] (sources [])
- step 1: expected reply to contain '14:00'

Transcript:

```
guest: What's the earliest I can get into my room?
  bot: I'm sorry, I can't confirm that from the information I have. Would you like me to ask a member of staff?
       {'intent': 'HOTEL_INFORMATION', 'language': 'en', 'grounded': False, 'handed_off': False}
```

### `paraphrase_dinner_time` (grounding)

Restaurant hours asked as "evening food".

- step 1: grounded False != True
- step 1: answer not based on ['restaurant'] (sources [])
- step 1: expected reply to contain '22:00'

Transcript:

```
guest: Until what time can we get food in the evening?
  bot: I'm sorry, I can't confirm that from the information I have. Would you like me to ask a member of staff?
       {'intent': 'HOTEL_INFORMATION', 'language': 'en', 'grounded': False, 'handed_off': False}
```

### `paraphrase_dog_cnr` (grounding)

Pet policy in Montenegrin with an inflected noun.

- step 1: grounded False != True
- step 1: answer not based on ['pets'] (sources [])
- step 1: expected reply to contain '10 EUR'

Transcript:

```
guest: Smijem li povesti psa?
  bot: Nažalost, to ne mogu potvrditi na osnovu informacija kojima raspolažem. Želite li da pitam nekoga od osoblja?
       {'intent': 'HOTEL_INFORMATION', 'language': 'cnr', 'grounded': False, 'handed_off': False}
```


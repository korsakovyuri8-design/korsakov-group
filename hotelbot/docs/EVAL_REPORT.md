# HOTELBOT evaluation report

Generated 2026-10-06 08:33 UTC by `python -m evals`. Deterministic: no LLM unless a scenario scripts one; synthetic property packs.

**TOTAL 154 · PASS 150 · FAIL 4**

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
| transactions | 43/43 |
| local | 23/23 |
| marketplace | 28/28 |

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
| ✅ | `mkt_taxi_four_with_luggage` | marketplace | yes |
| ✅ | `mkt_taxi_cheapest_suitable` | marketplace |  |
| ✅ | `mkt_taxi_child_seat` | marketplace | yes |
| ✅ | `mkt_taxi_vehicle_unavailable` | marketplace | yes |
| ✅ | `mkt_taxi_change_pickup_modified_in_place` | marketplace | yes |
| ✅ | `mkt_taxi_late_cancel_needs_consent` | marketplace | yes |
| ✅ | `mkt_taxi_late_cancel_confirmed` | marketplace |  |
| ✅ | `mkt_intercity_transfer` | marketplace |  |
| ✅ | `mkt_ski_missing_size` | marketplace | yes |
| ✅ | `mkt_ski_insufficient_inventory` | marketplace | yes |
| ✅ | `mkt_ski_last_item_competition` | marketplace | yes |
| ✅ | `mkt_ski_quote_expiry_releases_inventory` | marketplace | yes |
| ✅ | `mkt_bicycle_rental` | marketplace |  |
| ✅ | `mkt_ebike_rental_terms` | marketplace | yes |
| ✅ | `mkt_change_skis_to_snowboards` | marketplace | yes |
| ✅ | `mkt_unsupported_rental_category` | marketplace | yes |
| ✅ | `mkt_car_rental_terms` | marketplace | yes |
| ✅ | `mkt_car_minimum_duration` | marketplace | yes |
| ✅ | `mkt_guide_russian` | marketplace | yes |
| ✅ | `mkt_guide_language_unavailable` | marketplace | yes |
| ✅ | `mkt_guide_group_too_large` | marketplace | yes |
| ✅ | `mkt_guide_private_vs_group` | marketplace | yes |
| ✅ | `mkt_guide_weather_dependent` | marketplace | yes |
| ✅ | `mkt_guide_weather_called_off` | marketplace | yes |
| ✅ | `mkt_guide_late_cancellation` | marketplace | yes |
| ✅ | `mkt_hourly_city_tour` | marketplace |  |
| ✅ | `mkt_multi_transfer_skis_guide` | marketplace | yes |
| ✅ | `mkt_multi_cancel_one_keeps_others` | marketplace | yes |
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
| ✅ | `txn_unclear_change_goes_to_staff` | transactions | yes |
| ✅ | `txn_unsupported_external_capability` | transactions | yes |
| ✅ | `txn_llm_claims_booking_before_accepted` | transactions | yes |
| ✅ | `txn_status_before_acceptance` | transactions | yes |
| ✅ | `txn_quote_is_not_a_booking_status` | transactions | yes |
| ✅ | `txn_russian_arrival_transfer` | transactions |  |
| ✅ | `txn_montenegrin_flow` | transactions |  |
| ✅ | `txn_superseded_code_cannot_be_accepted` | transactions | yes |
| ✅ | `txn_quote_expiry_boundary` | transactions | yes |
| ✅ | `txn_quote_valid_until_last_minute` | transactions |  |
| ✅ | `txn_whatsapp_redelivery_of_consent` | transactions | yes |
| ✅ | `txn_lost_response_idempotent_provider` | transactions | yes |
| ✅ | `txn_lost_response_provider_without_idempotency` | transactions | yes |
| ✅ | `txn_worker_crash_after_provider_accepted` | transactions | yes |
| ✅ | `txn_completed_before_accepted` | transactions | yes |
| ✅ | `txn_callback_for_another_providers_booking` | transactions | yes |
| ✅ | `txn_unknown_outcome_reconciled_by_lookup` | transactions | yes |

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


## Adversarial transaction scenarios - raw first run (before fixes)

Written as desired behaviour, run once before any change: **TOTAL 42 · PASS 38 · GATES 32/36**. Verbatim failures:

```text
FAIL:
  transactions/txn_provider_timeout [GATE]
      - step 2: transaction.status = 'failed', expected 'submission_unknown'
      - step 2 notifications: expected reply to contain "can't confirm yet whether"
      - final: transactions ['failed'], expected ['submission_unknown']
  transactions/txn_superseded_code_cannot_be_accepted [GATE]
      - step 3: expected reply to contain one of ['replaced', 'no longer valid']
  transactions/txn_lost_response_provider_without_idempotency [GATE]
      - step 2: transaction.status = 'accepted', expected 'submission_unknown'
      - step 2 notifications: expected reply to contain "can't confirm yet whether"
      - step 2 notifications: FORBIDDEN claim 'has accepted' present
      - final: expected handoff {'reason': 'provider_failure'}, got []
      - final: transactions ['accepted'], expected ['submission_unknown']
      - final: provider holds 2 bookings, expected 1
      - final: provider received 2 submit calls, at most 1
  transactions/txn_callback_for_another_providers_booking [GATE]
      - step 4: transaction.status = 'accepted', expected 'submitted'
```

Diagnosis:

1. `txn_lost_response_provider_without_idempotency` - **real defect, double booking**: a lost response was retried against a provider that ignores idempotency keys -> 2 external bookings. Fixed by SUBMISSION_UNKNOWN (D-054).
2. `txn_provider_timeout` - **defect under the new policy** (timeout != failure): reported FAILED although the booking may exist. Fixed (D-054); this scenario's expectation was changed deliberately by that decision.
3. `txn_superseded_code_cannot_be_accepted` - **UX defect**: nothing was booked (correct), but the guest was not told the named offer had been replaced. Fixed (stale-code reply).
4. `txn_callback_for_another_providers_booking` - **harness defect**, not product: the callback was correctly refused (404) and final state was right; with a frozen clock two transactions share a timestamp and the step check read the wrong one. Fixed in the harness (prefers the transaction created in the step).

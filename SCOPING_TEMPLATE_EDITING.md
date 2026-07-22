# Scoping Template Editing Guide

This document explains how SpeakrBridge scoping templates work and how to edit them safely.

The current OpenText Fax template is defined in:

- [scoping/templates/open_text_fax_install_upgrade_2025-08-20.json](/home/astartz/Code/SpeakrBridge/scoping/templates/open_text_fax_install_upgrade_2025-08-20.json)

The template is a manifest that controls three things:

1. Which business answers the AI should extract from the meeting.
2. How those answers map to actual Word form controls.
3. Which deterministic rules should run after extraction.

## Mental Model

The scoping workflow has four layers:

1. **Sources**
SpeakrBridge collects `metadata`, `notes`, `speakr_summary`, and `transcript`. If the transcript contains the recap trigger phrase `Let me summarize this project from 50,000 feet`, the service also creates a high-priority `scoping_focus` source.

2. **Answers**
The template defines the business questions to extract, such as `telephony_type`, `mfp_brands`, or `training_types`.

3. **Derivation rules**
After the model returns answers, deterministic rules can fill in related answers. Example: if `mfp_brands` is found, the `mfp_module` choice is appended to `modules`.

4. **Word fields**
The final validated answers are translated into text fields, checkboxes, and dropdowns in the Word document.

## File Structure

Each template manifest contains these main sections:

- `project_modes`
- `answers`
- `derivation_rules`
- `fields`

## `project_modes`

`project_modes` defines workflow variants such as `install` and `upgrade`.

Each mode can preset Word values that should always be checked for that workflow. In the current template, this is how the install/upgrade project-type checkbox is chosen.

Use this section when:

- a form supports multiple project types
- some fields should always be preset by workflow rather than extracted from the call

## `answers`

`answers` is the most important section. It defines what the AI is expected to extract.

Each answer has:

- `id`: stable internal identifier
- `label`: user-facing question prompt
- `type`: `text`, `single_choice`, or `multi_choice`
- `choices`: allowed values for `single_choice` and `multi_choice`
- `guidance`: optional instruction to help the model fill the answer correctly
- `require_value_in_evidence`: optional text-only safeguard that requires the extracted value itself to appear in the grounded evidence quote
- `applies_to`: optional list of modes where the answer is relevant
- `extract`: whether the answer should be requested from the AI

### Answer Types

`text`
- Free-form grounded text.
- No fixed option list.
- Example: `mfp_brands`, `project_overview`, `module_comments`.

`single_choice`
- Exactly one allowed option.
- Must define `choices`.
- Example: `onsite_services_requested` with `yes` or `no`.

`multi_choice`
- Zero or more allowed options.
- Must define `choices`.
- Example: `modules`, `email_integrations`, `training_types`.

### Where Allowed Options Are Defined

For fixed-option answers, the allowed values live directly in `answers[].choices`.

Examples from the current template:

- `modules` choices define all valid module values.
- `email_integrations` choices define all valid email integration values.
- `training_types` choices define all valid training selections.

If a value is not in `choices`, it will be rejected during validation.

### What `guidance` Does

`guidance` helps the model interpret ambiguous meeting language.

Good uses of guidance:

- mapping customer wording to a known internal choice
- telling the model what to capture and what not to guess
- defining how to handle partial information

Example:

- `mfp_brands` guidance tells the model to list every explicitly referenced MFP brand and use `Brand not specified` only when MFP devices are in scope but the brand was not stated.

Important limitation:

- `guidance` is not a rule engine.
- It influences the model prompt, but it does not guarantee a result.
- If you need repeatable behavior, add or update a `derivation_rules` entry.

### What `require_value_in_evidence` Does

`require_value_in_evidence` is a stricter validation option for text answers that should be copied from the sources rather than paraphrased or guessed.

Use it for fields like:

- application names
- EMR names
- product names
- brand names
- other text answers where the exact grounded value matters

Example:

- if the evidence quote says `EMR in use is Meditech`, then a returned value of `Meditech` is allowed
- a returned value of `Epic` with that same evidence will be rejected and downgraded to `unknown`

This is useful when a text answer should behave more like a controlled extraction of a name, even though it is still stored as free text.

## `fields`

`fields` maps business answers into actual Word controls.

Each field has:

- `id`: stable field identifier
- `answer_id`: which answer populates this field
- `word_index`: the control index in the Word source form
- `type`: `text`, `checkbox`, or `dropdown`
- `label`: human-readable description
- `option_value`: for checkboxes only, which choice this checkbox represents
- `applies_to`: optional mode restriction

### How Field Mapping Works

Text answers:

- A `text` answer maps to one Word text field.

Single-choice answers:

- May map to one dropdown field, or
- May map to multiple checkboxes, where each checkbox corresponds to one allowed choice.

Multi-choice answers:

- Usually map to multiple checkboxes.
- Each checkbox uses `option_value` to represent one selectable choice.

### Important Rule

For checkbox fields, `option_value` must exactly match one of the answer's `choices`.

This is validated in the code. If the checkbox option does not match a declared choice, the template is invalid.

## `derivation_rules`

`derivation_rules` is the deterministic post-processing layer.

Use derivation rules when:

- the model may mention a concept in text, but you want a specific checkbox or choice set consistently
- one answer should imply another answer
- you want repeatable behavior without relying entirely on prompt wording

Each rule has:

- `id`
- `source_answer_id`
- `target_answer_id`
- either `match_any` or `when_source_found`
- optional `exclude_any`
- `target_value`
- `operation`
- optional `review_warning`

### Rule Triggers

`match_any`
- The rule runs only if the grounded evidence text for the source answer contains one of the listed terms.

`exclude_any`
- Prevents the rule from running if excluded terms are also present.

`when_source_found`
- Runs whenever the source answer is `found`, without needing keyword matching.

### Rule Operations

`set_if_missing`
- Set the target only if it is currently `unknown` or `inferred`.

`append`
- Add one or more values to a multi-choice target without removing existing valid values.

### Examples

`microsoft_365_oauth_email_integration`
- Looks at `email_comments`
- If grounded text contains `Office 365`, `Microsoft 365`, `O365`, or `M365`
- And does not mention `EWS`
- Then set `email_integrations` to `smtp_pop3_oauth`

`mfp_devices_require_mfp_module`
- Looks at `mfp_brands`
- If `mfp_brands` is found
- Append `mfp_module` to `modules`

### When To Use Rules Instead Of Guidance

Use `guidance` when:

- the answer itself should still be chosen by the model
- you are clarifying intent or scope

Use `derivation_rules` when:

- the same grounded phrase should always cause the same checkbox or choice selection
- you want consistency across many recordings

## Validation Rules

The template is strongly validated by [scoping/models.py](/home/astartz/Code/SpeakrBridge/scoping/models.py).

Important validation behavior:

- `text` answers cannot define `choices`
- `single_choice` and `multi_choice` answers must define `choices`
- answer IDs must be unique
- field IDs must be unique
- Word indexes must be complete and non-duplicated
- checkbox `option_value` must exist in the target answer's `choices`
- derivation rules must reference valid source and target answers
- derivation rule target values must be valid choices for the target answer

If a manifest violates these rules, the template will fail to load.

## How Extraction Works

The service builds a prompt from the `answers` section. The model must return one result per answer.

Each extracted answer has:

- `status`: `found`, `inferred`, or `unknown`
- `value`
- `confidence`
- `evidence`

### Status Meaning

`found`
- The answer is directly supported by an exact quote from one of the sources.

`inferred`
- The answer is a strong conclusion but not directly stated.

`unknown`
- The sources do not support an answer.

By default, only `found` answers are written into the Word document.

### Evidence Requirement

For `found`, the evidence quote must match actual source text. If the quote cannot be verified, the answer is downgraded to `unknown`.

This matters when tuning prompts:

- prompt wording can improve extraction rate
- but unsupported answers will still be rejected during validation

## The `scoping_focus` Recap

If the meeting includes the phrase:

- `Let me summarize this project from 50,000 feet`

the extractor treats that recap as a dedicated `scoping_focus` source. The prompt tells the model to treat it as the highest-priority recap for what should be captured in the worksheet, while still cross-checking the rest of the meeting content.

This is the best place to put the key scoping details you always want emphasized.

## How To Make Common Changes

### Add a New Fixed Choice

Example: add a new module option.

1. Add the new value to the target answer's `choices`.
2. Add a matching checkbox field with the same `option_value`.
3. Add guidance or a derivation rule if the model needs help selecting it.
4. Add or update tests.

### Improve Extraction for a Text Field

Example: `mfp_brands` is being missed.

1. Improve the answer's `guidance`.
2. Make sure the expected customer wording is likely to appear in the transcript or recap.
3. If the text should also trigger another field, add a derivation rule.
4. Add a test with real customer phrasing.

### Improve Extraction for a Checkbox or Choice

Example: `Office 365` should reliably check OAuth.

1. Confirm the target choice already exists in `choices`.
2. Add or refine a `derivation_rules` entry using `match_any` and `exclude_any`.
3. Add a test proving the rule fires.

### Add a New Free-Text Field

1. Add a new `text` answer.
2. Add a corresponding `text` field mapping.
3. Write clear `guidance` if the answer is subtle or easy to misunderstand.
4. Add tests that show grounded evidence populates the field.

## Safe Editing Guidelines

- Keep `id` values stable. Changing IDs breaks mappings and old extraction payloads.
- Prefer small edits. Change one behavior at a time and test it.
- Use `guidance` for interpretation, `choices` for fixed options, and `derivation_rules` for deterministic automation.
- Do not invent values that are not present in `choices`.
- Do not make text guidance too broad or the model will overfill fields.
- Use real meeting phrasing in tests whenever possible.

## Recommended Documentation Pattern For Each Answer

When documenting or reviewing an answer, capture these five things:

1. What question is this answer trying to capture?
2. Is it `text`, `single_choice`, or `multi_choice`?
3. If it is choice-based, what are the allowed values?
4. What customer wording should map to it?
5. Does it need a derivation rule for consistent checkbox behavior?

## Recommended Change Process

1. Update the manifest.
2. Review the answer type, choices, and field mapping together.
3. Review whether guidance alone is enough or if a derivation rule is needed.
4. Add or update unit tests in `tests/test_scoping_extraction.py`.
5. Run template inspection and extraction tests before using the template in production.

## Summary

When editing a scoping template:

- `answers` defines what the AI extracts
- `choices` defines allowed fixed options
- `guidance` explains how to interpret meeting language
- `derivation_rules` adds deterministic business logic
- `fields` maps validated answers into Word

If you remember that separation, the templates stay understandable and easy to tune.

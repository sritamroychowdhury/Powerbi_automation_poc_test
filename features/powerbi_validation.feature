Feature: Power BI automated validation -- Dispute Outcomes Summary
  As a QA lead I want automated checks that the "Dispute Outcomes Summary"
  report's KPIs match their Snowflake source and render correctly, with
  Claude reasoning over the results to give a clean pass/fail. Measures and
  baselines are sourced from Jira tickets, not from analyzing a local .pbix.

  Scenario Outline: KPI reconciles against Snowflake
    Given the business mapping for measure "<measure>" is loaded
    When Claude reconciles the Power BI value against Snowflake
    Then the reconciliation result should match

    Examples:
      | measure               |
      | total_disputed_amount |
      | win_rebate_dollars    |
      | loss_rebate_dollars   |
      | open_rebate_dollars   |
      | total_dispute_count   |
      | win_percentage        |

  Scenario: Dashboard visual matches the approved baseline
    Given the baseline screenshot for report "dispute_outcomes_summary" exists
    And the current dashboard screenshot for report "dispute_outcomes_summary" is captured
    When Claude compares the current screenshot against the baseline
    Then the visual comparison result should pass

  Scenario: Dispute status by dispute code chart reconciles against Snowflake
    Given the grouped business mapping for chart "dispute_status_by_dispute_code" is loaded
    When Claude reconciles the grouped Power BI values against Snowflake
    Then the grouped reconciliation result should match

  Scenario: Win, Loss, Open, and Unclassified dispute counts add up to the total
    Given the consistency check "dispute_status_components_sum_to_total" is loaded
    When the component counts are summed and compared against the total
    Then the components should sum to the total within tolerance

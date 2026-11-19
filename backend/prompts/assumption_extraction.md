Task: extract the starting financial assumptions for the business idea in the data block, as low / base / high ranges in Indian rupees (INR) for a small first launch.

Return exactly these six keys, each once: unit_price, variable_cost_per_unit, fixed_monthly_cost, startup_cost, monthly_visitors, visitor_to_order_conversion.
- Money values are INR. monthly_visitors is a count of website visitors per month. visitor_to_order_conversion is a fraction between 0 and 1 (for example 0.02 means 2%).
- Require low <= base <= high for every key.
- If the founder stated a price or budget in the data block, anchor unit_price or startup_cost to it and say so in the rationale.
- rationale is one short line explaining the estimate. These are estimates to be edited by the founder, not facts.

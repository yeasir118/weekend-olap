-- Question: How much revenue did we make each month? (OLAP-703: the Gold version)
-- Runs on the DuckDB warehouse. The rules in monthly_revenue_oltp.sql (time zone, revenue
-- definition, soft deletes, gap-filling, AOV) are already applied, and tested, in Gold.

select month, orders, revenue, avg_order_value, customers
from gold.mart_monthly_revenue
order by month;

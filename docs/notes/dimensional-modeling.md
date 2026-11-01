# Dimensional modeling (Kimball)

## Who is Kimball

Ralph Kimball is one of the founders of data warehousing. His 1996 book *The Data Warehouse
Toolkit* introduced **dimensional modelling**: star schemas, facts and dimensions, grain,
surrogate keys, slowly changing dimensions. Almost everything in this project's Gold layer
comes from it. The current edition is the 3rd (2013), co-written with Margy Ross.

The well-known counterpoint is **Bill Inmon**, who argued for a fully normalized central
warehouse first, with dimensional "data marts" built from it. "Kimball vs Inmon" is a classic
debate. Modern dbt and medallion setups are closer to Kimball: Silver is roughly the cleaned
and integrated layer, and Gold is Kimball's star schema.

The Kimball Group retired in 2015. Its website is an archive of about 20 years of articles
("Design Tips") written over time, not a course, which is why it feels disorganized. Read it
as a reference, not from top to bottom.

## Deciding what counts as a fact table

Facts come from **business events you want to measure**. Dimensions are the **context you
slice those measurements by**. Kimball turns that into a 4-step method, and there are reliable
rules of thumb for applying it.

**Kimball's 4 steps** (the "Fundamental concepts" section of the techniques page):

1. **Pick a business process:** something the business *does* that produces events. Ordering,
   paying, shipping, returning, a page view.
2. **Declare the grain:** what exactly one row represents. "One row per order line." This is
   the most important step; everything else follows from it.
3. **Identify the dimensions:** the who, what, where and when of each event. These become the keys.
4. **Identify the facts:** the numbers each event produces. These become the measures.

So you never ask "is this table a fact?" in isolation. You ask "what processes does the
business want to measure?" Each process at its grain becomes a fact table.

**Rules of thumb for sorting what you find:**

| Clue | Fact | Dimension |
|---|---|---|
| Part of speech | **A verb**, an event: *placed* an order, *made* a payment | **A noun**, a thing: customer, product, campaign, date |
| Main columns | Numbers you **sum or count** (amount, quantity) | Text you **filter or group by** (name, category, city) |
| Growth | Grows with every bit of activity, mostly appended | Grows slowly; rows get updated |
| Time | Each row happened at a moment | Exists over a period of time |
| Questions | "How much? How many?" | "By what? For whom? Which?" |

In the source system, the same split usually shows up as **transactions vs master data**.
Applied to this project's tables:
- `orders`, `order_items`, `payments` → events → **facts**
- `users`, `products` → things → **dimensions**
- the campaign CSV → a thing → **dimension**

**Kimball's planning tool, the bus matrix:** rows are business processes, columns are
dimensions, and a ✓ means "this process can be sliced by this dimension." For this project:

| Process (grain) | Date | Customer | Product | Campaign |
|---|:-:|:-:|:-:|:-:|
| Ordering (one row per order) → `fct_orders` | ✓ | ✓ | | ✓ |
| Ordering (one row per line) → `fct_order_items` | ✓ | ✓ | ✓ | ✓ |
| Paying (one row per payment) → `fct_payments` | ✓ | ✓ | | |

Reading it tells you a lot:
- Every row is a fact table, and every column is a dimension.
- **Shared columns are "conformed dimensions":** the same `dim_customers` serves all three
  facts, so "revenue by customer" and "payments by customer" agree.
- An empty cell shows a grain limit. You can't slice `fct_orders` by product, because one
  order has many products. That's exactly why `fct_order_items` exists: decision D1, which
  fixed the outline's product query.

**Grain decides how many fact tables you need.** Never mix grains in one table. Orders and
order lines are the *same process at two grains*, so they're two fact tables. If you put
`order_total` on every line row, summing it would multiply each order's total by its number of lines.

**Three kinds of fact table:**
- **Transaction:** one row per event. All three of ours are this kind.
- **Periodic snapshot:** one row per entity per period, capturing a *state*. Examples: stock
  level per product per day, account balance per month. You'd pick this when the question is
  "what was the level?" rather than "what happened?"
- **Accumulating snapshot:** one row per process instance, updated as it moves through
  milestones. Example: one row per order with `ordered_date`, `paid_date`, `shipped_date`,
  `days_to_ship`. You'd pick this when the question is about durations through a pipeline.

There's also a **factless fact**: an event with no numbers, where the row itself is the
measurement. Examples: a student attending a class, a customer becoming eligible for a
promotion. You `count(*)` it.

**The grey area: the same entity can be both.** A customer is a dimension, but *signing up* is
an event. If the business asks "signups per month by country," that's a fact (or a simple
count from `dim_customers.signup_date`, which is fine at this scale). The question decides,
not the table. **What people measure becomes a fact; what they slice by becomes a dimension.**

**To practise,** imagine the app added three tables and sort them yourself:
- `reviews` (customer, product, rating, written_at)
- `inventory_levels` (product, warehouse, quantity_on_hand, as of each night)
- `shipments` (order, carrier, shipped_at, delivered_at)

What kind of fact table would each be, at what grain, and with which dimensions? (Answers, in
the same order as the three kinds above: transaction, periodic snapshot, and accumulating
snapshot, or a transaction fact per shipment event.) Working through new tables like this is
how the decision becomes instinct.

## Reading list (free)

1. **[Kimball's Dimensional Modeling Techniques](https://www.kimballgroup.com/data-warehouse-business-intelligence-resources/kimball-techniques/dimensional-modeling-techniques/)**:
   the one organized page on the Kimball site. It's a structured summary of the techniques
   from the Toolkit (3rd edition), grouped into sections, with a PDF download. Each entry is a
   short paragraph. Read these sections:
   - **Fundamental concepts**: the 4-step design process (business process → grain → dimensions → facts)
   - **Basic fact table techniques**: grain, additive vs non-additive measures
   - **Basic dimension table techniques**: surrogate keys, the calendar date dimension,
     "aggregated facts as dimension attributes"
   - **Slowly changing dimensions**: Types 0–7. Focus on Types 1 and 2 (OLAP-901).

   Skip "advanced" for now (bridge tables, mini-dimensions and so on).
2. **[Building a Kimball dimensional model with dbt](https://docs.getdbt.com/blog/kimball-dimensional-model)**
   on the dbt blog, by Jonathan Neo: a step-by-step tutorial that follows Kimball's process in
   dbt. Its [companion repository](https://github.com/Data-Engineer-Camp/dbt-dimensional-modelling)
   uses dbt Core with DuckDB, almost the same stack as this project. A good way to compare
   this Gold layer with someone else's.
3. **The book itself**, for depth later: *The Data Warehouse Toolkit, 3rd ed.* Chapters 1–3
   (fundamentals and a retail sales case study) cover most of what this project uses. Not
   free, but it's on [O'Reilly Learning](https://www.oreilly.com/library/view/the-kimball-group/9781119216315/c06.xhtml)
   (check whether work provides access), and many public libraries have it.

Fit it into the project: read the fact-table sections right before building facts, and the
slowly changing dimensions section before deciding on OLAP-901. Concepts stick better next to
the code that uses them.

# Visual datasets

- `people/<person>/`: several face and full-body views with different light and clothes.
- `places/<room>/`: doorway, center and reverse views; repeat after major furniture changes.
- `artifacts/<artifact>/`: stable household objects that Sokol should locate.

Images are runtime data under `/data/sokol_memory/vision` and are not sent to an API until a task needs them.

# Site backlog

## Desktop poet page timeline scroll

Source request: `http://127.0.0.1:4322/kobiyal/poets/jibanananda-das/`

On desktop, the left timeline/phase column should support independent scroll when the pointer is over that column and the user scrolls there. The main/right column can either keep following the active left-column section or remain independent, but clicking a left-column section must still scroll the corresponding right-column section into view.

Notes:

- Scope this to desktop layout only.
- Preserve current section-click behavior.
- Check sticky positioning and scroll-spy behavior after the change.

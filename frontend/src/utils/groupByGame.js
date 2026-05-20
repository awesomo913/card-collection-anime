// Group collection items (cards or sealed) into per-game display sections.
// Used by the Cards and Sealed list pages so both stay consistent. Empty
// sections are dropped; anything whose game isn't recognised falls into a
// trailing "Other" bucket (e.g. items still tagged before game backfill ran).

export const GAME_SECTIONS = [
  ['magic', 'Magic'],
  ['pokemon', 'Pokémon'],
  ['yugioh', 'Yu-Gi-Oh!'],
];

export const groupByGame = (items) => {
  const known = new Set(GAME_SECTIONS.map(([k]) => k));
  const sections = GAME_SECTIONS.map(([key, label]) => ({
    key,
    label,
    items: items.filter((it) => (it.game || '').toLowerCase() === key),
  }));
  const other = items.filter((it) => !known.has((it.game || '').toLowerCase()));
  if (other.length) sections.push({ key: 'other', label: 'Other', items: other });
  return sections.filter((s) => s.items.length > 0);
};

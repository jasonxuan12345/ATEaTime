import type { Dataset, Selection } from './model';

const searchIndexes = new WeakMap<Dataset['parts'], string[]>();
const partIndexes = new WeakMap<Dataset['parts'], { sites: Map<number, number | null>; counts: Map<number, number> }>();

export function filterParts(parts: Dataset['parts'], search: string) {
  if (!search) return parts;
  let index = searchIndexes.get(parts);
  if (!index) {
    index = parts.map(p => `${p.id} ${p.name} site ${p.site} bin ${p.bin}`.toLowerCase());
    searchIndexes.set(parts, index);
  }
  const query = search.toLowerCase();
  return parts.filter((_, i) => index[i].includes(query));
}

export function selectedSites(dataset: Dataset, selection: Selection): Set<number> {
  const excluded = selection.excludedParts ?? [];
  if (selection.parts === null && !excluded.length) return new Set(selection.sites.filter(s => dataset.sites.includes(s)));
  let index = partIndexes.get(dataset.parts);
  if (!index) {
    const sites = new Map<number, number | null>(), counts = new Map<number, number>();
    for (const part of dataset.parts) {
      sites.set(part.id, part.site);
      if (part.site !== null) counts.set(part.site, (counts.get(part.site) ?? 0) + 1);
    }
    index = { sites, counts }; partIndexes.set(dataset.parts, index);
  }
  if (selection.parts !== null) {
    const sites = new Set(selection.parts.map(id => index.sites.get(id)));
    return new Set(selection.sites.filter(s => sites.has(s)));
  }
  const counts = new Map(index.counts);
  for (const id of excluded) {
    const site = index.sites.get(id);
    if (site != null) counts.set(site, (counts.get(site) ?? 0) - 1);
  }
  return new Set(selection.sites.filter(s => (counts.get(s) ?? 0) > 0));
}

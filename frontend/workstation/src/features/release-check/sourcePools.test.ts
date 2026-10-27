import { changeSources, demoDataset, initialSelections, pooledDataset } from './model';

const small = { ...demoDataset, id: 'small', filename: '8-sites.stdf', sites: [1,2,3,4,5,6,7,8],
  parts: [{ id: 1, site: 1, bin: 1, name: 'repeat' }, { id: 7, site: 8, bin: 1, name: 'repeat' }] };
const large = { ...small, id: 'large', filename: '16-sites.stdf', sites: Array.from({length:16},(_,i)=>i+1),
  parts: [{ id: 1, site: 16, bin: 1, name: 'repeat' }, { id: 4, site: 1, bin: 1, name: 'repeat' }] };

it('shows actual sites through site 16 and assigns distinct records with file provenance', () => {
  const pool = pooledDataset([small, large])!;
  expect(pool.sites).toEqual(large.sites);
  expect(pool.parts.map(part=>part.id)).toEqual([1,7,9,12]);
  expect(pool.parts[2]).toMatchObject({sourceId:'large',sourcePartId:1,filename:large.filename});
});

it('preserves exclusions and choices when adding and removing files with repeated local IDs', () => {
  const selection = { ...initialSelections(small).cpk, sites: [1], excludedParts: [1] };
  const joined = changeSources([small, large], selection, ['small','large']);
  expect(joined.datasetIds).toEqual(['small','large']);
  expect(joined.sites).toEqual([1,9,10,11,12,13,14,15,16]);
  expect(joined.excludedParts).toEqual([1]);
  const excluded = {...joined, excludedParts:[1,9]};
  const remaining = changeSources([small,large],excluded,['large']);
  expect(remaining.excludedParts).toEqual([1]);
  expect(remaining.datasetIds).toEqual(['large']);
  expect(remaining.parts).toBeNull();
});

it('keeps explicit part selections attached to the original file after pool reindexing', () => {
  const joined = changeSources([small,large],initialSelections(small).cpk,['small','large']);
  const remaining = changeSources([small,large],{...joined,parts:[7,12]},['large']);
  expect(remaining.parts).toEqual([4]);
  expect(remaining.excludedParts).toEqual([]);
});

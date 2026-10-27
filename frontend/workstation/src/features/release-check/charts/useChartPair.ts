import { useEffect, useState } from 'react';
import type { Module, Row } from '../model';
import { chartScenario, type ChartPair } from './chartScenarios';
import type { ChartKind } from './types';

export function useChartPair(owner: string, row: Row, module: Module) {
  const [choice, setChoice] = useState<(ChartPair & { owner: string }) | null>(null);
  useEffect(() => setChoice(null), [owner]);
  const scenario = chartScenario(row, module);
  const manual = choice?.owner === owner;
  const pair = manual ? choice! : scenario;
  const write = (next: ChartPair) => setChoice({ ...next, owner });
  const swap = () => write({ primary: pair.secondary, secondary: pair.primary, primaryStd: pair.secondaryStd, secondaryStd: pair.primaryStd });
  function select(role: 'primary' | 'secondary', kind: ChartKind, stdMode = false) {
    const other = role === 'primary' ? 'secondary' : 'primary';
    if (pair[role] === kind) return;
    if (pair[other] === kind) { swap(); return; }
    if (role === 'primary' && kind === 'wafer') {
      write({ primary: kind, primaryStd: false, secondary: pair.primary, secondaryStd: pair.primaryStd });
    } else write({ ...pair, [role]: kind, [role + 'Std']: stdMode });
  }
  return { ...pair, scenario, manual, select, swap, restore: () => setChoice(null),
    toggleStd: (role: 'primary' | 'secondary') => write({ ...pair, [role + 'Std']: !pair[role === 'primary' ? 'primaryStd' : 'secondaryStd'] }) };
}

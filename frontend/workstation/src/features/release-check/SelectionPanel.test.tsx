import { render, screen } from "@testing-library/react";
import { SelectionPanel } from "./SelectionPanel";
import { demoDataset, initialSelections } from "./model";

it("warns when selected Site Diff records only cover one site without blocking other checks", () => {
  const selection = { ...initialSelections(demoDataset).mmsa, parts: [1, 5, 9] };
  render(<SelectionPanel dataset={demoDataset} module="mmsa" selection={selection} disabled={false} onChange={vi.fn()} />);
  expect(screen.getByRole("note")).toHaveTextContent("当前记录范围只有 1 个 site");
  expect(screen.getByRole("note")).toHaveTextContent("CPK 和 CGM 仍可计算");
});

it("does not apply Site Diff requirements to a single-site CGM selection", () => {
  const selection = { ...initialSelections(demoDataset).cgm, sites: [1] };
  render(<SelectionPanel dataset={demoDataset} module="cgm" selection={selection} disabled={false} onChange={vi.fn()} />);
  expect(screen.queryByRole("note")).not.toBeInTheDocument();
});

it("removes the warning when the selected records cover two sites", () => {
  const selection = { ...initialSelections(demoDataset).mmsa, parts: [0, 1] };
  render(<SelectionPanel dataset={demoDataset} module="mmsa" selection={selection} disabled={false} onChange={vi.fn()} />);
  expect(screen.queryByRole("note")).not.toBeInTheDocument();
});

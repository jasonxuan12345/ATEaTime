import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { ReleaseCheckPage } from "./ReleaseCheckPage";
import { demoDataset, demoRun, startingDemoSelections } from "./model";
import { emptyOutlierReport } from './outliers/testFixtures';

const a = { ...demoDataset, id: "source-a", filename: "capability.stdf" };
const b = { ...demoDataset, id: "source-b", filename: "repeatability.stdf", sites: [11],
  tests: [{ column: "B_TEST", number: 5001, name: "B_TEST_FULL_NAME", unit: "A" }],
  parts: [{ id: 1, site: 11, bin: 2, name: "B_PART" }], record_count: 1 };
const result = { ...demoRun(startingDemoSelections()), id: "run-ab", dataset_id: a.id };
const ok = (data: unknown) => ({ ok: true, json: async () => data });
const file = (name: string) => new File(["stdf"], name);
function mount() { return render(<MemoryRouter><ReleaseCheckPage /></MemoryRouter>); }
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('uses filename checkboxes to pool CPK files and permits independent module opt-out', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(ok(a)).mockResolvedValueOnce(ok(b))
    .mockResolvedValueOnce(ok(result)).mockResolvedValue(ok({ row: result.rows[0], details: {} }));
  vi.stubGlobal('fetch',fetch);mount();const user=userEvent.setup();
  await user.upload(screen.getByLabelText('选择 STDF 文件'),[file(a.filename),file(b.filename)]);
  await screen.findByRole('checkbox',{name:`${b.filename} 用于 cpk`});
  await user.click(screen.getByRole('checkbox',{name:'bin1 CPK site 4'}));
  await user.click(screen.getByRole('checkbox',{name:`${b.filename} 用于 cpk`}));
  expect(screen.getByRole('checkbox',{name:`${a.filename} 用于 cpk`})).toBeChecked();
  expect(screen.getByRole('checkbox',{name:'bin1 CPK site 11'})).toBeChecked();
  expect(screen.getByRole('checkbox',{name:'bin1 CPK site 4'})).not.toBeChecked();
  expect(screen.queryByRole('radio')).not.toBeInTheDocument();
  expect(screen.queryByRole('combobox',{name:/源文件/})).not.toBeInTheDocument();
  for(const role of ['cgm','mmsa','outlier']) await user.click(screen.getByRole('checkbox',{name:`${a.filename} 用于 ${role}`}));
  await user.click(screen.getByRole('button',{name:'计算 Release 检查'}));
  await screen.findByText('计算完成',{exact:true});
  const body=JSON.parse(fetch.mock.calls.find(([url])=>String(url).startsWith('/api/release-check/runs?'))![1].body);
  expect(body.cpk.dataset_ids).toEqual([a.id,b.id]);
  expect(body.cpk.sites).toEqual([1,2,3,11]);
  expect(body.cpk.test_columns).toContain('B_TEST');
  expect(body.cgm).toBeUndefined();expect(body.mmsa).toBeUndefined();
  expect(fetch.mock.calls.some(([url])=>String(url).includes('/outliers?'))).toBe(false);
});

it("imports multiple files and sends each module's own source and selections", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(ok(a)).mockResolvedValueOnce(ok(b))
    .mockResolvedValueOnce(ok(result)).mockImplementation(async (url: string) => ok(url.includes('/outliers?') ? emptyOutlierReport : { row: result.rows[0], details: {} }));
  vi.stubGlobal("fetch", fetch); mount(); const user = userEvent.setup();
  await user.upload(screen.getByLabelText("选择 STDF 文件"), [file(a.filename), file(b.filename)]);
  await screen.findByRole("checkbox", { name: `${b.filename} 用于 cpk` });
  expect(screen.queryByRole("combobox", { name: /源文件/ })).not.toBeInTheDocument();
  await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'CGM'}));
  await user.click(screen.getByRole("checkbox", { name: `${a.filename} 用于 cgm` }));
  await user.click(screen.getByRole("checkbox", { name: `${b.filename} 用于 cgm` }));
  expect(screen.getByRole("checkbox", { name: "CGM site 11" })).toBeChecked();
  expect(screen.queryByRole("checkbox", { name: "CGM site 1" })).not.toBeInTheDocument();
  await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'Site Diff'}));
  expect(screen.getByRole("checkbox", { name: `${a.filename} 用于 mmsa` })).toBeChecked();
  await user.click(screen.getByRole("button", { name: "bin1 CPK" }));
  expect(screen.queryByRole("combobox", { name: /源文件/ })).not.toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "bin1 CPK site 1" })).toBeChecked();
  await user.click(screen.getByRole("button", { name: "计算 Release 检查" }));
  await screen.findByText("计算完成", { exact: true });
  const call = fetch.mock.calls.find(([url]) => String(url).startsWith('/api/release-check/runs?'))!;
  const body = JSON.parse(call[1].body);
  expect(body.cpk.dataset_ids).toEqual([a.id]);
  expect(body.cpk.sites).toEqual([1, 2, 3, 4]);
  expect(body.cgm.dataset_ids).toEqual([b.id]);
  expect(body.cgm.sites).toEqual([11]);
  expect(body.cgm.test_columns).toEqual(["B_TEST"]);
  expect(body.mmsa.dataset_ids).toEqual([a.id]);
  expect(body.mmsa.sites).toEqual([1, 2, 3, 4]);
  expect(fetch.mock.calls.some(([url]) => url === "/api/release-check/datasets/source-a")).toBe(false);
});

it("adding a file preserves selections; removing a selected source leaves the other modules usable", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(ok(a)).mockResolvedValueOnce(ok(b)).mockResolvedValue(ok({}));
  vi.stubGlobal("fetch", fetch); mount(); const user = userEvent.setup();
  await user.upload(screen.getByLabelText("选择 STDF 文件"), file(a.filename));
  await screen.findByRole("checkbox", { name: `${a.filename} 用于 cpk` });
  await user.click(screen.getByRole("checkbox", { name: "bin1 CPK site 4" }));
  await user.upload(screen.getByLabelText("选择 STDF 文件"), file(b.filename));
  await screen.findByRole("checkbox", { name: `${b.filename} 用于 cpk` });
  expect(screen.getByRole("checkbox", { name: "bin1 CPK site 4" })).not.toBeChecked();
  await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'CGM'}));
  await user.click(screen.getByRole("checkbox", { name: `${a.filename} 用于 cgm` }));
  await user.click(screen.getByRole("checkbox", { name: `${b.filename} 用于 cgm` }));
  await user.click(screen.getByRole("button", { name: `移除文件 ${b.filename}` }));
  expect(screen.queryByRole("checkbox", { name: "CGM site 11" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "计算 Release 检查" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "导出报告 JSON" })).toBeDisabled();
  expect(fetch).toHaveBeenCalledWith("/api/release-check/datasets/source-b", { method: "DELETE" });
});

it("retains successful files and reports a failed file in a multi-file import", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(ok(a)).mockResolvedValueOnce({ ok: false, status: 422,
    json: async () => ({ detail: "损坏的 STDF" }) });
  vi.stubGlobal("fetch", fetch); mount(); const user = userEvent.setup();
  await user.upload(screen.getByLabelText("选择 STDF 文件"), [file(a.filename), file("bad.stdf")]);
  expect(await screen.findByRole("alert")).toHaveTextContent("bad.stdf：损坏的 STDF");
  expect(screen.getByRole("checkbox", { name: `${a.filename} 用于 cpk` })).toBeVisible();
  expect(screen.getByRole("button", { name: "保存 Parquet" })).toBeEnabled();
});

it("releases every real file on leaving the page", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(ok(a)).mockResolvedValueOnce(ok(b)).mockResolvedValue(ok({}));
  vi.stubGlobal("fetch", fetch); const view = mount();
  await userEvent.setup().upload(screen.getByLabelText("选择 STDF 文件"), [file(a.filename), file(b.filename)]);
  await screen.findByRole("checkbox", { name: `${b.filename} 用于 cpk` });
  view.unmount();
  expect(fetch).toHaveBeenCalledWith("/api/release-check/datasets/source-a", { method: "DELETE", keepalive: true });
  expect(fetch).toHaveBeenCalledWith("/api/release-check/datasets/source-b", { method: "DELETE", keepalive: true });
});

it("releases a late upload result after leaving instead of leaking a session", async () => {
  let resolve!: (value: unknown) => void;
  const fetch = vi.fn().mockImplementationOnce(() => new Promise(r => { resolve = r; })).mockResolvedValue(ok({}));
  vi.stubGlobal("fetch", fetch); const view = mount();
  await userEvent.setup().upload(screen.getByLabelText("选择 STDF 文件"), file(a.filename));
  view.unmount(); resolve(ok(a));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/release-check/datasets/source-a", { method: "DELETE" }));
});

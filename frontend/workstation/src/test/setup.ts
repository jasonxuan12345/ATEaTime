import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

vi.mock("echarts", () => ({
  init: vi.fn(() => ({
    dispose: vi.fn(),
    dispatchAction: vi.fn(),
    getDataURL: vi.fn(() => "data:image/png;base64,test"),
    hideLoading: vi.fn(),
    off: vi.fn(),
    on: vi.fn(),
    resize: vi.fn(),
    setOption: vi.fn(),
    showLoading: vi.fn(),
  })),
}));

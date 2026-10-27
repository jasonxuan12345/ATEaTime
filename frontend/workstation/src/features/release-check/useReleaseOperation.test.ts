import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { request } from './model';
import { useReleaseOperation } from './useReleaseOperation';

vi.mock('./model', () => ({ request: vi.fn() }));
beforeEach(() => { vi.useFakeTimers(); vi.mocked(request).mockReset(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('polls measured progress and clears it after the download ends', async () => {
  vi.mocked(request).mockResolvedValue({ phase: '生成第 3 / 6 页', progress: 45 });
  const { result } = renderHook(() => useReleaseOperation());
  let job!: ReturnType<typeof result.current.start>;
  act(() => { job = result.current.start(); });
  expect(result.current.progress).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(request).toHaveBeenCalledWith(`/jobs/${job.id}`, { signal: expect.any(AbortSignal) });
  expect(result.current.progress).toBe(45);
  expect(result.current.phase).toBe('生成第 3 / 6 页');
  act(() => result.current.finish(job));
  expect(result.current.progress).toBeNull();
  expect(result.current.active).toBe(false);
});

it('leaves progress indeterminate when a job only reports a phase', async () => {
  vi.mocked(request).mockResolvedValue({ phase: '正在准备数据' });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }));
  const { result } = renderHook(() => useReleaseOperation());
  act(() => { result.current.start(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(result.current.phase).toBe('正在准备数据');
  expect(result.current.progress).toBeNull();
});

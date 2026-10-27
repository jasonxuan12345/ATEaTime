import { useCallback, useEffect, useRef, useState } from 'react';
import { request } from './model';

type ActiveOperation = { id: string; controller: AbortController };

export function useReleaseOperation() {
  // Parallel imports run several jobs at once; the newest stays primary for
  // phase polling while every job stays cancellable until it finishes.
  const operations = useRef(new Map<string, ActiveOperation>());
  const [operation, setOperation] = useState<ActiveOperation | null>(null);
  const [phase, setPhase] = useState(''), [cancelling, setCancelling] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  useEffect(() => {
    if (!operation) return;
    const controller = new AbortController();
    let pending = false;
    const poll = async () => {
      if (pending) return;
      pending = true;
      try {
        const status = await request<{ phase: string; progress?: number | null }>(`/jobs/${operation.id}`, { signal: controller.signal });
        if (!controller.signal.aborted && operations.current.get(operation.id) === operation && !operation.controller.signal.aborted) {
          setPhase(status.phase);
          setProgress(typeof status.progress === 'number' ? Math.max(0, Math.min(100, status.progress)) : null);
        }
      } catch { /* The job is absent while uploading or after completion. */ }
      finally { pending = false; }
    };
    const timer = setInterval(() => void poll(), 500);
    return () => { clearInterval(timer); controller.abort(); };
  }, [operation]);
  useEffect(() => {
    const release = () => {
      for (const current of operations.current.values()) {
        void fetch(`/api/release-check/jobs/${current.id}`, { method: 'DELETE', keepalive: true }).catch(() => undefined);
        current.controller.abort();
      }
      operations.current.clear();
    };
    const hide = (event: PageTransitionEvent) => { if (!event.persisted) release(); };
    window.addEventListener('pagehide', hide);
    return () => { window.removeEventListener('pagehide', hide); release(); };
  }, []);
  const start = useCallback(() => {
    const id = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    const current = { id, controller: new AbortController() };
    operations.current.set(id, current); setOperation(current); setPhase(''); setProgress(null); setCancelling(false);
    return current;
  }, []);
  const finish = useCallback((current: ActiveOperation) => {
    if (!operations.current.delete(current.id)) return;
    setOperation(previous => previous === current ? [...operations.current.values()].at(-1) ?? null : previous);
    if (!operations.current.size) { setPhase(''); setProgress(null); setCancelling(false); }
  }, []);
  const cancel = useCallback(async () => {
    const all = [...operations.current.values()];
    if (!all.length) return;
    setCancelling(true); setPhase('正在取消…');
    try {
      await Promise.all(all.map(async current => {
        const response = await fetch(`/api/release-check/jobs/${current.id}`, { method: 'DELETE' });
        if (!response.ok) throw new Error('cancel failed');
        current.controller.abort();
      }));
    } catch {
      setPhase('取消请求未送达，请重试'); setCancelling(false);
    }
  }, []);
  return { start, finish, cancel, phase, progress, cancelling, active: operation !== null };
}

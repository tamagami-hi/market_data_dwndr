import { afterEach, describe, expect, test, vi } from "vitest";

import { createPollController } from "@/hooks/polling";

afterEach(() => {
  vi.useRealTimers();
});

describe("poll controller", () => {
  test("never overlaps requests and schedules after completion", async () => {
    vi.useFakeTimers();
    let resolveRequest: (() => void) | undefined;
    const task = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveRequest = resolve;
        }),
    );
    const controller = createPollController({ task, intervalMs: () => 1_000 });

    controller.start();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(task).toHaveBeenCalledTimes(1);

    resolveRequest?.();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(999);
    expect(task).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(task).toHaveBeenCalledTimes(2);
    controller.stop();
  });

  test("aborts in-flight work when stopped", async () => {
    const task = vi.fn((signal: AbortSignal) => {
      expect(signal.aborted).toBe(false);
      return new Promise<void>(() => undefined);
    });
    const controller = createPollController({ task, intervalMs: () => 1_000 });

    controller.start();
    await Promise.resolve();
    controller.stop();

    expect(task.mock.calls[0][0].aborted).toBe(true);
  });

  test("pauses while hidden and refreshes immediately when visible", async () => {
    vi.useFakeTimers();
    let isHidden = true;
    const task = vi.fn(async () => undefined);
    const controller = createPollController({
      task,
      intervalMs: () => 1_000,
      isPaused: () => isHidden,
    });

    controller.start();
    await Promise.resolve();
    expect(task).not.toHaveBeenCalled();

    isHidden = false;
    controller.resume();
    await Promise.resolve();
    expect(task).toHaveBeenCalledTimes(1);
    controller.stop();
  });

  test("times out hung work and backs off after failures", async () => {
    vi.useFakeTimers();
    const onTimeout = vi.fn();
    const task = vi.fn(
      (signal: AbortSignal) =>
        new Promise<void>((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(new DOMException("Timed out", "AbortError")));
        }),
    );
    const controller = createPollController({
      task,
      intervalMs: () => 1_000,
      errorIntervalMs: () => 5_000,
      timeoutMs: 2_000,
      onTimeout,
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(2_000);
    expect(onTimeout).toHaveBeenCalledTimes(1);
    expect(task.mock.calls[0][0].aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(4_999);
    expect(task).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(task).toHaveBeenCalledTimes(2);
    controller.stop();
  });
});

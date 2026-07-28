export interface PollController {
  start(): void;
  stop(): void;
  resume(): void;
}

export function createPollController(options: {
  task: (signal: AbortSignal) => Promise<void>;
  intervalMs: () => number;
  errorIntervalMs?: () => number;
  isPaused?: () => boolean;
  timeoutMs?: number;
  onTimeout?: () => void;
}): PollController {
  let isActive = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let abortController: AbortController | null = null;
  let isRunning = false;

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const schedule = (hasFailed = false) => {
    if (!isActive || options.isPaused?.()) return;
    clearTimer();
    const interval = hasFailed && options.errorIntervalMs
      ? options.errorIntervalMs()
      : options.intervalMs();
    timer = setTimeout(() => void run(), interval);
  };

  const run = async () => {
    if (!isActive || isRunning || options.isPaused?.()) return;
    isRunning = true;
    abortController = new AbortController();
    let hasFailed = false;
    let didTimeout = false;
    const timeout = options.timeoutMs
      ? setTimeout(() => {
          didTimeout = true;
          options.onTimeout?.();
          abortController?.abort();
        }, options.timeoutMs)
      : null;
    try {
      await options.task(abortController.signal);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        hasFailed = true;
      }
    } finally {
      if (timeout !== null) clearTimeout(timeout);
      hasFailed ||= didTimeout;
      isRunning = false;
      abortController = null;
      schedule(hasFailed);
    }
  };

  return {
    start() {
      if (isActive) return;
      isActive = true;
      void run();
    },
    stop() {
      isActive = false;
      clearTimer();
      abortController?.abort();
    },
    resume() {
      if (!isActive || isRunning) return;
      clearTimer();
      void run();
    },
  };
}

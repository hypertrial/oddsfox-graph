/// <reference types="vite/client" />

import type { RecordingStory } from "./types";

declare global {
  interface Window {
    __ODDSFOX_RECORDING__?: {
      readonly ready: boolean;
      getStory: () => RecordingStory;
      getFrameCount: () => number;
      seek: (frame: number) => Promise<void>;
    };
  }
}

export {};

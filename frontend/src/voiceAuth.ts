// On-device speaker verification via Picovoice Eagle, so hands-free voice mode only acts on the
// enrolled owner's voice. All inference runs locally in the browser (WASM); only the AccessKey
// is validated against Picovoice. The model is served from /eagle_params.pv (public/).
import { EagleProfilerWorker, EagleWorker } from "@picovoice/eagle-web";
import type { EagleProfile } from "@picovoice/eagle-web";
import { WebVoiceProcessor } from "@picovoice/web-voice-processor";

const EAGLE_MODEL = { publicPath: "/eagle_params.pv", forceWrite: true };

export const voiceAuthSupported = () =>
  typeof window !== "undefined" &&
  Boolean(navigator.mediaDevices?.getUserMedia) &&
  typeof window.WebAssembly !== "undefined";

export const bytesToBase64 = (bytes: Uint8Array): string => {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
};

export const base64ToBytes = (b64: string): Uint8Array => {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
};

// Engine objects subscribed to WebVoiceProcessor receive 16kHz Int16 frames via postMessage.
type WvpFrameEngine = { postMessage: (event: { command: string; inputFrame: Int16Array }) => void };

// Enroll the current speaker from the mic until 100%, returning the voice profile as base64.
export async function enrollSpeaker(
  accessKey: string,
  onProgress: (percent: number) => void
): Promise<string> {
  if (!accessKey.trim()) throw new Error("Add your Picovoice AccessKey first.");
  await WebVoiceProcessor.reset().catch(() => undefined);
  const profiler = await EagleProfilerWorker.create(accessKey, EAGLE_MODEL);

  let finished = false;
  let resolveDone!: (value: string) => void;
  let rejectDone!: (reason: Error) => void;
  const done = new Promise<string>((resolve, reject) => {
    resolveDone = resolve;
    rejectDone = reject;
  });

  const engine: WvpFrameEngine = {
    postMessage: (event) => {
      if (event.command !== "process" || finished) return;
      profiler
        .enroll(event.inputFrame)
        .then(async (percent) => {
          onProgress(Math.min(100, Math.round(percent)));
          if (percent >= 100 && !finished) {
            finished = true;
            const profile = await profiler.export();
            resolveDone(bytesToBase64(profile.bytes));
          }
        })
        .catch((error) => {
          if (!finished) {
            finished = true;
            rejectDone(error as Error);
          }
        });
    },
  };

  try {
    WebVoiceProcessor.setOptions({ frameLength: profiler.frameLength });
    await WebVoiceProcessor.subscribe(engine);
    return await done;
  } finally {
    await WebVoiceProcessor.unsubscribe(engine).catch(() => undefined);
    profiler.terminate();
  }
}

export type VoiceAuthRecognizer = { stop: () => Promise<void> };

// Continuously score mic audio against the enrolled profile. onScore receives the owner's
// similarity in [0, 1] for each processed frame (higher = more likely the enrolled speaker).
export async function startRecognizer(
  accessKey: string,
  profileBase64: string,
  onScore: (score: number) => void
): Promise<VoiceAuthRecognizer> {
  if (!accessKey.trim() || !profileBase64) throw new Error("Voice authentication is not set up.");
  const eagle = await EagleWorker.create(accessKey, EAGLE_MODEL);
  const profile: EagleProfile = { bytes: base64ToBytes(profileBase64) };
  let stopped = false;

  const engine: WvpFrameEngine = {
    postMessage: (event) => {
      if (event.command !== "process" || stopped) return;
      eagle
        .process(event.inputFrame, [profile])
        .then((scores) => {
          if (!stopped && scores && scores.length) onScore(scores[0]);
        })
        .catch(() => undefined);
    },
  };

  WebVoiceProcessor.setOptions({ frameLength: eagle.minProcessSamples });
  await WebVoiceProcessor.subscribe(engine);

  return {
    stop: async () => {
      stopped = true;
      await WebVoiceProcessor.unsubscribe(engine).catch(() => undefined);
      eagle.terminate();
    },
  };
}

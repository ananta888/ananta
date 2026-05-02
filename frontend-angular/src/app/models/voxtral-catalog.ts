export interface VoxtralModelPreset {
  id: string;
  label: string;
  fileName: string;
  url: string;
  sizeHint: string;
  minBytes?: number;
  recommended?: boolean;
}

export const VOXTRAL_MODEL_PRESETS: VoxtralModelPreset[] = [
  {
    id: 'voxtral-mini-4b-q4-0-community',
    label: 'Voxtral Mini 4B Q4_0 (kleinstes kompatibles Preset)',
    fileName: 'Q4_0.gguf',
    url: 'https://huggingface.co/andrijdavid/Voxtral-Mini-4B-Realtime-2602-GGUF/resolve/main/Q4_0.gguf',
    sizeHint: '~2.3 GB',
    minBytes: 1024 * 1024 * 1024,
    recommended: true,
  },
  {
    id: 'voxtral-mini-4b-q4-k',
    label: 'Voxtral Mini 4B Q4_K',
    fileName: 'voxtral-mini-4b-realtime-q4_k.gguf',
    url: 'https://huggingface.co/cstr/voxtral-mini-4b-realtime-GGUF/resolve/main/voxtral-mini-4b-realtime-q4_k.gguf',
    sizeHint: '~2.7 GB',
    minBytes: 1024 * 1024 * 1024,
  },
  {
    id: 'voxtral-mini-4b-q5-k',
    label: 'Voxtral Mini 4B Q5_K',
    fileName: 'Q5_K.gguf',
    url: 'https://huggingface.co/andrijdavid/Voxtral-Mini-4B-Realtime-2602-GGUF/resolve/main/Q5_K.gguf',
    sizeHint: '~2.9 GB',
    minBytes: 1200 * 1024 * 1024,
  },
  {
    id: 'voxtral-mini-4b-q8-0',
    label: 'Voxtral Mini 4B Q8_0',
    fileName: 'voxtral-mini-4b-realtime-q8_0.gguf',
    url: 'https://huggingface.co/cstr/voxtral-mini-4b-realtime-GGUF/resolve/main/voxtral-mini-4b-realtime-q8_0.gguf',
    sizeHint: '~4.9 GB',
    minBytes: 2 * 1024 * 1024 * 1024,
  },
];

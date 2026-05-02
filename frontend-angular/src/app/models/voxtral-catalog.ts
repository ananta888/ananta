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
    id: 'voxtral-mini-4b-q2-k-community',
    label: 'Voxtral Mini 4B Q2_K (Community, experimentell)',
    fileName: 'Q2_K.gguf',
    url: 'https://huggingface.co/andrijdavid/Voxtral-Mini-4B-Realtime-2602-GGUF/resolve/main/Q2_K.gguf',
    sizeHint: '~1.4 GB',
    minBytes: 700 * 1024 * 1024,
  },
  {
    id: 'voxtral-mini-4b-q3-k-community',
    label: 'Voxtral Mini 4B Q3_K (Community, experimentell)',
    fileName: 'Q3_K.gguf',
    url: 'https://huggingface.co/andrijdavid/Voxtral-Mini-4B-Realtime-2602-GGUF/resolve/main/Q3_K.gguf',
    sizeHint: '~1.8 GB',
    minBytes: 900 * 1024 * 1024,
  },
  {
    id: 'voxtral-mini-4b-q4-k',
    label: 'Voxtral Mini 4B Q4_K',
    fileName: 'voxtral-mini-4b-realtime-q4_k.gguf',
    url: 'https://huggingface.co/cstr/voxtral-mini-4b-realtime-GGUF/resolve/main/voxtral-mini-4b-realtime-q4_k.gguf',
    sizeHint: '~2.7 GB',
    minBytes: 1024 * 1024 * 1024,
    recommended: true,
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

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
    id: 'voxtral-mini-4b-q3-k',
    label: 'Voxtral Mini 4B Realtime Q3_K (kleinstes sicheres APK-Preset)',
    fileName: 'Q3_K.gguf',
    url: 'https://huggingface.co/andrijdavid/Voxtral-Mini-4B-Realtime-2602-GGUF/resolve/main/Q3_K.gguf',
    sizeHint: '~1.9 GB',
    minBytes: 1_900_000_000,
    recommended: true,
  },
  {
    id: 'voxtral-mini-4b-q4-k',
    label: 'Voxtral Mini 4B Realtime Q4_K (nur bei viel freiem RAM)',
    fileName: 'voxtral-mini-4b-realtime-q4_k.gguf',
    url: 'https://huggingface.co/cstr/voxtral-mini-4b-realtime-GGUF/resolve/main/voxtral-mini-4b-realtime-q4_k.gguf',
    sizeHint: '~2.4 GB',
    minBytes: 2_400_000_000,
  },
  {
    id: 'voxtral-mini-4b-q4-0-community',
    label: 'Voxtral Mini 4B Q4_0 (Community, Kompatibilitaet pruefen)',
    fileName: 'Q4_0.gguf',
    url: 'https://huggingface.co/andrijdavid/Voxtral-Mini-4B-Realtime-2602-GGUF/resolve/main/Q4_0.gguf',
    sizeHint: '~2.3 GB',
    minBytes: 2_000_000_000,
  },
  {
    id: 'voxtral-mini-4b-q5-k',
    label: 'Voxtral Mini 4B Q5_K',
    fileName: 'Q5_K.gguf',
    url: 'https://huggingface.co/andrijdavid/Voxtral-Mini-4B-Realtime-2602-GGUF/resolve/main/Q5_K.gguf',
    sizeHint: '~2.9 GB',
    minBytes: 2_600_000_000,
  },
  {
    id: 'voxtral-mini-4b-q8-0',
    label: 'Voxtral Mini 4B Q8_0',
    fileName: 'voxtral-mini-4b-realtime-q8_0.gguf',
    url: 'https://huggingface.co/cstr/voxtral-mini-4b-realtime-GGUF/resolve/main/voxtral-mini-4b-realtime-q8_0.gguf',
    sizeHint: '~4.9 GB',
    minBytes: 4_000_000_000,
  },
];

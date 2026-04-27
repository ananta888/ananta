export interface VoxtralModelPreset {
  id: string;
  label: string;
  fileName: string;
  url: string;
  sizeHint: string;
  recommended?: boolean;
}

export const VOXTRAL_MODEL_PRESETS: VoxtralModelPreset[] = [
  {
    id: 'voxtral-mini-4b-q4-k',
    label: 'Voxtral Mini 4B Q4_K',
    fileName: 'voxtral-mini-4b-realtime-q4_k.gguf',
    url: 'https://huggingface.co/cstr/voxtral-mini-4b-realtime-GGUF/resolve/main/voxtral-mini-4b-realtime-q4_k.gguf',
    sizeHint: '~2.7 GB',
    recommended: true,
  },
  {
    id: 'voxtral-mini-4b-q5-k',
    label: 'Voxtral Mini 4B Q5_K',
    fileName: 'voxtral-mini-4b-realtime-q5_k.gguf',
    url: 'https://huggingface.co/cstr/voxtral-mini-4b-realtime-GGUF/resolve/main/voxtral-mini-4b-realtime-q5_k.gguf',
    sizeHint: '~3.2 GB',
  },
  {
    id: 'voxtral-mini-4b-q8-0',
    label: 'Voxtral Mini 4B Q8_0',
    fileName: 'voxtral-mini-4b-realtime-q8_0.gguf',
    url: 'https://huggingface.co/cstr/voxtral-mini-4b-realtime-GGUF/resolve/main/voxtral-mini-4b-realtime-q8_0.gguf',
    sizeHint: '~4.9 GB',
  },
];

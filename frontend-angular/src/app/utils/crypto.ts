export function encrypt(text: string): string {
  const key = 'ananta-secret-key'; // Einfacher Schlüssel für XOR
  let result = '';
  for (let i = 0; i < text.length; i++) {
    result += String.fromCharCode(text.charCodeAt(i) ^ key.charCodeAt(i % key.length));
  }
  return btoa(result);
}

export function decrypt(encoded: string): string {
  try {
    const text = atob(encoded);
    const key = 'ananta-secret-key';
    let result = '';
    for (let i = 0; i < text.length; i++) {
      result += String.fromCharCode(text.charCodeAt(i) ^ key.charCodeAt(i % key.length));
    }
    // Backward compatibility: keep plaintext tokens untouched.
    if (encrypt(result) !== encoded) {
      return encoded;
    }
    return result;
  } catch (e) {
    return encoded; // Fallback, falls es nicht verschlüsselt war
  }
}

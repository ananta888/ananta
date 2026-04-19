import { Injectable } from '@angular/core';

import { generateJWT } from '../utils/jwt';

@Injectable({ providedIn: 'root' })
export class AgentJwtService {
  createFrontendToken(sharedSecret: string): Promise<string> {
    return generateJWT(
      { sub: 'frontend', iat: Math.floor(Date.now() / 1000) },
      sharedSecret,
    );
  }
}

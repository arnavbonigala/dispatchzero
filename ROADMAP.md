# Kinship Hackathon Roadmap

**The one feature:** Mom gets a call from "her son." She taps **Verify**. His real phone receives a challenge, he approves it with Face ID, and her screen turns green. A deepfake caller can't answer the challenge, so her screen turns red. The whole demo rests on this one loop working every time.

## Hour 0: lock contracts together (30 min), then split

These are the only shared surfaces. Freeze them so all four tracks can run in parallel.

```
Pairing payload (QR/NFC):   { v:1, pairId, name, pubKey: <P-256 JWK>, relayUrl }
Challenge (relay → prover): { type:"challenge", pairId, nonce: 32B b64, ts, ttl:30 }
Response (prover → relay):  { type:"response", pairId, nonce, sig: b64 }
```

- **Crypto:** WebCrypto ECDSA P-256 with non-extractable keys stored in IndexedDB. Works in every browser, Safari included, with zero libraries.
- **App:** one PWA served from a single Vite app, so both phones just open a URL. No native apps.

## Parallel tracks

| Track | Builds | Done when |
|---|---|---|
| **A. Crypto/protocol** | Keygen, sign, verify, nonce + TTL check, replay rejection (seen-nonce set) | An `assert` script passes: valid sig, wrong key, expired, and replayed cases |
| **B. Relay** | WebSocket server with rooms keyed by `pairId`. Forwards messages only and never sees keys (Node `ws` or a Cloudflare Durable Object) | Two browser tabs exchange messages through the deployed relay |
| **C. Pairing** | Show a QR of your payload, scan theirs, store the contact. Web NFC tap on Android as a stretch goal | Two phones each hold the other's public key |
| **D. Verify UX + demo** | Verify button, prover approval screen gated by a WebAuthn passkey prompt (the Face ID moment), green and red result screens, cloned-voice attacker clip, pitch script | The full demo runs end to end 5 times in a row |

**Dependencies:** A, B, and C start immediately. D builds against mocks and wires in A and B around hour 6.

## Milestones

1. **~H6:** Two phones paired over QR, challenge and signature round-trip through the relay (ugly is fine).
2. **~H12:** Full demo loop on real phones over the deployed relay.
3. **~H16:** Polish the Face ID approval, the green and red states, and the attack demo (replay an old signature and show the rejection).
4. **Last 4h:** Feature freeze. Rehearse the demo and record a backup video.

## Cut list

Accounts, contact list UI beyond one pair, push notifications (keep both apps open during the demo), call integration or audio analysis, key rotation, multi-device. Each one becomes a single line in the "what's next" part of the pitch.

## Depth signals for judges

- Replay protection and the 30s TTL, demonstrated live.
- The private key never leaves the device's secure storage, and the relay is treated as untrusted.
- An attacker who controls the relay still can't forge a green check.

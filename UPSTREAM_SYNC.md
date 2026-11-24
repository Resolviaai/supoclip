# Upstream Synchronization & Feature Preservation Protocol

This document defines the automated synchronization procedure for keeping this repository updated with the official upstream repository (`https://github.com/FujiwaraChoki/supoclip.git`) while strictly protecting Resolvia-customized features and maintaining a permanent **$0.00 operational cost**.

---

## ⚡ Trigger Prompt

Whenever you want an AI agent to perform a sync, copy and paste this exact prompt:

```text
Sync SupoClip with official upstream repo and preserve Resolvia features
```

When an agent sees this trigger prompt, it must read this file (`UPSTREAM_SYNC.md`) and execute the Standard Operating Procedure (SOP) below.

---

## 🛡️ Non-Negotiable Core Principles

### 1. Permanent $0.00 Operational Cost
- **Never replace Gemini free-tier transcription with paid third-party providers** (e.g. Soniox, AssemblyAI paid tiers).
- Keep `TRANSCRIPTION_PROVIDER=gemini` with silence-bounded chunking and drop-out/starvation recovery as default.
- Keep Cloudflare R2 object storage ($0.00 tier with zero egress fees) and GitHub Actions automated background publisher.

### 2. Strict Exclusion of Hosted / SaaS Features
Do **NOT** pull or merge upstream commits related to:
- **Stripe Connect & Affiliate Payouts** (e.g. Creator commission payouts, webhook endpoints).
- **Payment Providers & Subscriptions** (e.g. Pix checkouts, billing recovery emails, team seat-based pricing).
- **Auth & Access Restrictions** (e.g. Email verification gates on signup, account lockdowns).
- **Paid Transcription Providers** (e.g. Soniox async transcription, unless explicitly requested).

### 3. Protected Resolvia Features (NEVER Overwrite or Regress)
1. **Gemini Chunked Transcription & Recovery** (`backend/src/media/gemini_transcription.py`):
   - Silence-bounded segment splitting (`_detect_silence_split_points`).
   - Internal/trailing speech dropout detection (>35-second gap).
   - Low-WPM speech starvation detection (<30 words per minute) with automatic fallback to high-resilience models (`gemini-2.5-flash-lite`).
2. **Brand & Vocabulary Normalization** (`backend/src/media/transliteration.py`):
   - Strict preservation of the brand name `"SupoClip"`.
   - Preservation of standard English spelling for technical loanwords (e.g., Premiere Pro, PC, transitions, clients, courses).
   - Clean Roman Hinglish transliteration for Hindi speech.
3. **Multi-Clip Batch Social Publishing** (`frontend/src/components/social/batch-publish-modal.tsx` & `backend/src/services/batch_scheduler.py`):
   - Multi-select clip batch modal supporting YouTube Shorts, Instagram Reels, Facebook, LinkedIn, and X.
   - Anti-bot drip scheduling with randomized human jitter (`generateDripSchedule`).
   - Granular per-clip caption overrides and batch status tracking.
4. **Cloudflare R2 & 24/7 Background Publisher** (`.github/workflows/publish-scheduled.yml`):
   - Autonomous headless posting runner executing every 15 minutes.
   - S3-compatible R2 direct video upload and presigned URL distribution.
5. **Mobile PWA & Touch Ergonomics** (`frontend/src/app/globals.css`):
   - Safe-area inset padding (`env(safe-area-inset-top)`, `env(safe-area-inset-bottom)`).
   - Overscroll suppression and tactile touch response (`active:scale-98`).

### 4. Public Repository Guardrails
- **Never commit real secrets, private customer IDs, emails, Stripe keys, or cloud credentials.**
- All test fixtures must use synthetic IDs and `example.com` dummy addresses.

---

## 📋 Standard Operating Procedure (SOP)

When the trigger prompt is issued, follow these exact steps:

### Phase 1: Fetch and Inspect Upstream
1. Ensure git remote `upstream` exists:
   ```bash
   git remote get-url upstream || git remote add upstream https://github.com/FujiwaraChoki/supoclip.git
   ```
2. Fetch the latest changes from upstream:
   ```bash
   git fetch upstream main
   ```
3. Inspect commits since the last merge base:
   ```bash
   git log HEAD..upstream/main --oneline
   ```

### Phase 2: Filter and Triage Commits
Categorize new commits into two buckets:

* **Bucket A: Safe to Pull (High Value, Zero Risk)**
  - Local video reframing algorithms (e.g., split-screen caption seam positioning, face-collision avoidance for subtitles).
  - FFmpeg and cut boundary improvements (e.g., scene change detection in busy footage, removing edge flash-frames).
  - Self-hosted quality of life improvements (e.g., removing video duration limits on self-hosted instances).
  - General UI layout bugs that do not touch auth or billing.

* **Bucket B: Strict Skip (Do NOT Pull)**
  - Any commits touching Stripe, checkout, Pix, creator commissions, or subscription plans.
  - Any commits introducing email verification or registration gates.
  - Any commits adding commercial/paid transcription providers (Soniox, etc.).

### Phase 3: Apply on an Isolated Scratch Branch
1. Create a clean branch from `resolvia-features`:
   ```bash
   git checkout resolvia-features
   git checkout -b sync/upstream-upgrade-<date>
   ```
2. Cherry-pick or merge vetted changes from Bucket A only.
3. If conflicts arise in shared files (e.g., `CHANGELOG.md` or `config.py`), resolve them by keeping our custom Gemini/R2/batch publishing settings intact.

### Phase 4: Automated Verification Checklist
Execute all validation checks:
1. **Gemini Transcription Tests**:
   ```bash
   docker run --rm -v "c:/CODE/supoclip/backend:/backend" -w /backend supoclip-worker /app/.venv/bin/pytest --no-cov tests/unit/test_gemini_transcription.py
   ```
2. **Frontend Typecheck & Lint**:
   ```bash
   docker exec supoclip-frontend pnpm run typecheck
   docker exec supoclip-frontend pnpm run lint
   ```
3. **Secret Scanner**:
   Verify no API keys or private tokens are staged (`git diff --staged`).

### Phase 5: Merge and Document
1. Merge the scratch branch into `resolvia-features`:
   ```bash
   git checkout resolvia-features
   git merge sync/upstream-upgrade-<date>
   git branch -d sync/upstream-upgrade-<date>
   ```
2. Update `CHANGELOG.md` under the current date with plain-language bullet points and no private identifiers.
3. Push to `origin/resolvia-features`.

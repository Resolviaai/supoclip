export type SocialProviderId = "youtube" | "tiktok" | "instagram" | "linkedin" | "twitter" | "facebook";

export type SocialPrivacyLevel = "public" | "unlisted" | "private";

export type SocialPostStatus =
  | "scheduled"
  | "queued"
  | "publishing"
  | "published"
  | "failed"
  | "cancelled";

export interface SocialProviderStatus {
  provider: SocialProviderId;
  display_name: string;
  configured: boolean;
  /** Turned off for now by the `tiktok_publishing` feature flag; reported as unconfigured too. */
  paused?: boolean;
  supported_privacy_levels: SocialPrivacyLevel[];
  default_privacy_level?: SocialPrivacyLevel;
  setup_hint: string | null;
}

export interface SocialConnection {
  id: string;
  provider: SocialProviderId;
  external_account_id: string;
  display_name: string | null;
  username: string | null;
  avatar_url: string | null;
  token_expires_at: string | null;
  status: "active" | "reauth_required" | "error" | "revoked";
  last_error: string | null;
  created_at: string | null;
}

export interface SocialPostMetrics {
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  saves: number | null;
}

export interface SocialPost {
  id: string;
  task_id: string | null;
  clip_id: string | null;
  social_account_id: string | null;
  provider: SocialProviderId;
  status: SocialPostStatus;
  scheduled_for: string | null;
  title: string | null;
  caption: string | null;
  hashtags: string[];
  privacy_level: SocialPrivacyLevel;
  external_post_id: string | null;
  external_url: string | null;
  error_message: string | null;
  attempts: number;
  hook_type: string | null;
  hook_title: string | null;
  virality_score: number | null;
  clip_duration: number | null;
  published_at: string | null;
  last_metrics_at: string | null;
  metrics: SocialPostMetrics | null;
  created_at: string | null;
  account: {
    display_name: string | null;
    username: string | null;
    avatar_url: string | null;
    status: string | null;
  };
}

export interface HookTypePerformance {
  hook_type: string;
  posts: number;
  avg_views: number | null;
  median_views: number | null;
  avg_likes: number | null;
  avg_comments: number | null;
  avg_shares: number | null;
  avg_duration: number | null;
  avg_predicted_score: number | null;
  avg_engagement_rate: number | null;
}

export interface ProviderPerformance {
  provider: SocialProviderId;
  posts: number;
  measured_posts: number;
  total_views: number | null;
  avg_views: number | null;
  total_likes: number | null;
  total_comments: number | null;
  total_shares: number | null;
}

export interface DurationPerformance {
  bucket: string;
  posts: number;
  avg_views: number | null;
}

export interface PerformanceSummary {
  total_published: number;
  measured_posts: number;
  by_hook_type: HookTypePerformance[];
  by_provider: ProviderPerformance[];
  by_duration: DurationPerformance[];
  top_posts: SocialPost[];
  min_posts_for_personalization: number;
  personalization_active: boolean;
}

export const PROVIDER_LABELS: Record<SocialProviderId, string> = {
  youtube: "YouTube Shorts",
  tiktok: "TikTok",
  instagram: "Instagram Reels",
  linkedin: "LinkedIn",
  twitter: "X (Twitter)",
  facebook: "Facebook",
};

export const PRIVACY_LABELS: Record<SocialPrivacyLevel, string> = {
  public: "Public",
  unlisted: "Unlisted",
  private: "Private (only me)",
};

export const POST_STATUS_LABELS: Record<SocialPostStatus, string> = {
  scheduled: "Scheduled",
  queued: "Queued",
  publishing: "Publishing",
  published: "Published",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const ACTIVE_POST_STATUSES: SocialPostStatus[] = ["queued", "publishing"];

export function connectionLabel(connection: Pick<SocialConnection, "display_name" | "username" | "provider">): string {
  const handle = connection.username ? `@${connection.username.replace(/^@/, "")}` : null;
  const name = connection.display_name;
  if (name && handle && name !== handle) return `${name} (${handle})`;
  return name || handle || PROVIDER_LABELS[connection.provider];
}

export function formatMetric(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `${(value / 1_000).toFixed(1)}k`;
  return Math.round(value).toLocaleString();
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

export function parseHashtagInput(value: string): string[] {
  return value
    .split(/[\s,]+/)
    .map((tag) => tag.replace(/^#/, "").trim())
    .filter(Boolean);
}

export function durationBucketLabel(bucket: string): string {
  switch (bucket) {
    case "under_20s":
      return "Under 20s";
    case "20_to_35s":
      return "20–35s";
    case "35_to_50s":
      return "35–50s";
    case "over_50s":
      return "Over 50s";
    default:
      return bucket;
  }
}

export type DripStrategy = "immediate" | "every_3_hours" | "daily_peak";

/**
 * Generates human-like staggered schedule dates for batch clip publishing.
 * Injects natural timing jitter (±4 to 12 minutes) to ensure platform algorithms
 * perceive uploads as organic, authentic creator activity rather than robotic automation.
 */
export function generateDripSchedule(
  count: number,
  strategy: DripStrategy,
  startDate: Date = new Date(),
): (string | null)[] {
  const dates: (string | null)[] = [];
  const baseTime = startDate.getTime();

  for (let i = 0; i < count; i++) {
    if (strategy === "immediate") {
      // First clip is immediate; subsequent clips get a 90-second buffer
      // to prevent same-account rate limiting / robotic burst flags.
      if (i === 0) {
        dates.push(null);
      } else {
        const bufferedTime = new Date(baseTime + i * 90 * 1000);
        dates.push(bufferedTime.toISOString());
      }
      continue;
    }

    // Natural random jitter: -6 to +11 minutes in milliseconds
    const jitterMs = (Math.floor(Math.random() * 18) - 6) * 60 * 1000;

    if (strategy === "every_3_hours") {
      const offsetMs = (i + 1) * 3 * 60 * 60 * 1000 + jitterMs;
      const scheduled = new Date(baseTime + Math.max(offsetMs, 5 * 60 * 1000));
      dates.push(scheduled.toISOString());
    } else if (strategy === "daily_peak") {
      // Target peak engagement hours: 18:30 (6:30 PM local)
      const targetDate = new Date(startDate);
      targetDate.setDate(targetDate.getDate() + i);
      targetDate.setHours(18, 30, 0, 0);

      // If the target peak time today is already in the past, push by +1 day
      if (targetDate.getTime() <= baseTime + 15 * 60 * 1000) {
        targetDate.setDate(targetDate.getDate() + 1);
      }

      const scheduledWithJitter = new Date(targetDate.getTime() + jitterMs);
      dates.push(scheduledWithJitter.toISOString());
    }
  }

  return dates;
}
